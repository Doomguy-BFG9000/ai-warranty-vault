"""Grounded answering: citations must resolve, and abstention must be possible."""

from __future__ import annotations

import uuid

from app.mock_chat import ScriptedChatModel, push_response
from app.rag.answer import (
    NO_ANSWER_TEXT,
    compose_answer,
    enforce_grounding,
    render_facts,
    render_sources,
    resolve_citations,
)
from app.rag.retriever import RetrievedChunk


def _chunk(text: str, *, page: int | None = 3, title: str = "Karta gwarancyjna") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        page_number=page,
        locator=f"page {page}" if page else "characters 1–100",
        content=text,
        token_count=len(text.split()),
        score=0.5,
        vector_similarity=0.5,
        lexical_rank=0.2,
        vector_position=1,
        lexical_position=1,
        document_title=title,
    )


def test_a_valid_label_resolves_to_the_servers_own_record():
    chunks = [_chunk("Okres gwarancji wynosi 24 miesiace."), _chunk("Wylaczenia.", page=4)]
    citations, dropped = resolve_citations(["S2"], chunks)

    assert dropped == []
    assert len(citations) == 1
    # Every field comes from the database row, not from the model.
    assert citations[0]["chunk_id"] == str(chunks[1].chunk_id)
    assert citations[0]["page_number"] == 4
    assert citations[0]["locator"] == "page 4"
    assert citations[0]["document_title"] == "Karta gwarancyjna"


def test_labels_that_were_not_in_this_turns_context_are_dropped():
    chunks = [_chunk("one"), _chunk("two")]
    citations, dropped = resolve_citations(["S1", "S9", "page 12", "", "S0"], chunks)

    assert [citation["label"] for citation in citations] == ["S1"]
    # S9 is out of range, "page 12" is not a label, S0 is 1-based nonsense.
    assert set(dropped) == {"S9", "page 12", "", "S0"}


def test_a_model_supplied_uuid_is_not_accepted_as_a_citation():
    """The model never handles ids, so an id-shaped string is not a citation."""
    chunks = [_chunk("one")]
    citations, dropped = resolve_citations([str(uuid.uuid4())], chunks)
    assert citations == []
    assert len(dropped) == 1


def test_duplicate_labels_collapse():
    chunks = [_chunk("one"), _chunk("two")]
    citations, _ = resolve_citations(["S1", "S1", "[S1]"], chunks)
    assert len(citations) == 1


def test_an_answer_with_no_valid_citation_is_downgraded_to_no_answer():
    chunks = [_chunk("Okres gwarancji wynosi 24 miesiace.")]
    outcome, answer, citations = enforce_grounding(
        "answered", "Gwarancja obejmuje wszystko.", [], chunks
    )
    assert outcome == "no_answer"
    assert answer == NO_ANSWER_TEXT
    assert citations == []


def test_with_no_sources_at_all_the_outcome_is_no_answer_whatever_the_model_said():
    outcome, answer, citations = enforce_grounding(
        "answered", "Yes, it is covered for 5 years.", [{"label": "S1"}], []
    )
    assert outcome == "no_answer"
    assert citations == []


def test_a_conflicting_sources_outcome_survives_when_it_is_cited():
    chunks = [_chunk("Nie obejmuje przewodu."), _chunk("Cable covered for 12 months.", page=3)]
    citations, _ = resolve_citations(["S1", "S2"], chunks)
    outcome, answer, kept = enforce_grounding(
        "conflicting_sources", "The two documents disagree.", citations, chunks
    )
    assert outcome == "conflicting_sources"
    assert len(kept) == 2


async def test_no_retrieved_chunks_short_circuits_without_calling_the_model():
    """No context means the model is not asked at all — there is only one right answer."""
    model = ScriptedChatModel()
    push_response({"outcome": "answered", "answer": "Sure, 5 years.", "cited_sources": ["S1"]})

    result = await compose_answer(chat_model=model, question="Ile lat gwarancji?", chunks=[])

    assert result.outcome == "no_answer"
    assert result.answer == NO_ANSWER_TEXT
    # The scripted reply was never consumed.
    from app.mock_chat import pending_responses

    assert pending_responses() == 1


async def test_a_fabricated_citation_from_the_model_becomes_an_abstention():
    model = ScriptedChatModel()
    push_response(
        {
            "outcome": "answered",
            "answer": "Gwarancja obejmuje uszkodzenia mechaniczne (S7, strona 12).",
            "cited_sources": ["S7"],
        }
    )
    chunks = [_chunk("Okres gwarancji wynosi 24 miesiace od daty zakupu.")]

    result = await compose_answer(chat_model=model, question="Czy obejmuje uderzenie?", chunks=chunks)

    assert result.outcome == "no_answer"
    assert result.citations == []
    assert result.dropped_labels == ["S7"]
    assert "mechaniczne" not in result.answer


async def test_a_well_cited_answer_is_returned_with_server_built_citations():
    model = ScriptedChatModel()
    push_response(
        {
            "outcome": "answered",
            "answer": "Gwarancja trwa 24 miesiace od daty zakupu (S1).",
            "cited_sources": ["S1"],
        }
    )
    chunks = [_chunk("Okres gwarancji wynosi 24 miesiace od daty zakupu.", page=1)]

    result = await compose_answer(chat_model=model, question="Ile trwa gwarancja?", chunks=chunks)

    assert result.outcome == "answered"
    assert len(result.citations) == 1
    assert result.citations[0]["page_number"] == 1
    assert result.citations[0]["chunk_id"] == str(chunks[0].chunk_id)


async def test_a_provider_failure_is_raised_not_papered_over():
    """No fallback answer, no fabricated citation — the error propagates."""
    model = ScriptedChatModel()
    push_response(RuntimeError("provider exploded"))
    chunks = [_chunk("Some text.")]

    try:
        await compose_answer(chat_model=model, question="?", chunks=chunks)
    except RuntimeError as exc:
        assert "provider exploded" in str(exc)
    else:
        raise AssertionError("a provider failure must not be turned into an answer")


def test_the_sources_block_is_labelled_and_complete():
    chunks = [_chunk("First clause.", page=2), _chunk("Second clause.", page=None)]
    rendered = render_sources(chunks)
    assert "[S1]" in rendered and "[S2]" in rendered
    assert "page 2" in rendered
    # A page-less source shows its character range rather than a made-up page.
    assert "characters" in rendered
    assert "First clause." in rendered and "Second clause." in rendered


def test_computed_facts_are_labelled_as_computed_and_carry_the_caveat():
    rendered = render_facts(
        {"purchase_date": "2025-03-14", "warranty_expires_at": "2027-03-14", "warranty_window": "active"}
    )
    assert "computed by this system" in rendered
    # The sentence that stops an open window being read as "this fault is covered".
    assert "not evidence" in rendered
    assert "2027-03-14" in rendered

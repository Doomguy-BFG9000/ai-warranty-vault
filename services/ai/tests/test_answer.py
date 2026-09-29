"""Grounded answering: citations must resolve, and abstention must be possible."""

from __future__ import annotations

import uuid

from app.mock_chat import ScriptedChatModel, push_response
from app.rag.answer import (
    NO_ANSWER_TEXT,
    compose_answer,
    enforce_grounding,
    render_conflict,
    render_facts,
    render_sources,
    resolve_citations,
    resolve_conflict_positions,
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
    outcome, answer, citations, used_facts = enforce_grounding(
        "answered", "Gwarancja obejmuje wszystko.", [], chunks
    )
    assert outcome == "no_answer"
    assert answer == NO_ANSWER_TEXT
    assert citations == []
    assert used_facts is False


def test_with_nothing_at_all_the_outcome_is_no_answer_whatever_the_model_said():
    outcome, answer, citations, _ = enforce_grounding(
        "answered", "Yes, it is covered for 5 years.", [{"label": "S1"}], []
    )
    assert outcome == "no_answer"
    assert citations == []


# ── the second grounding channel ────────────────────────────────────────────
# Regression: "Ile mam jeszcze gwarancji?" abstained even though the product record
# held the exact answer, because no passages had been retrieved. A computed date is
# grounded — in arithmetic over the user's own saved fields — and no document could
# ever answer it.

FACTS = {
    "purchase_date": "2025-03-14",
    "warranty_expires_at": "2027-03-14",
    "days_remaining": 531,
    "warranty_window": "active",
}


def test_the_saved_product_record_is_grounding_on_its_own():
    outcome, answer, citations, used_facts = enforce_grounding(
        "answered",
        "Gwarancja trwa do 14.03.2027, czyli zostalo 531 dni.",
        [],
        [],  # nothing retrieved
        facts=FACTS,
        used_product_facts=True,
    )
    assert outcome == "answered"
    assert "531" in answer
    assert citations == []
    # Reported separately, so the app never renders a computed date as a quotation.
    assert used_facts is True


def test_claiming_the_product_record_without_one_is_still_ungrounded():
    outcome, _answer, _citations, used_facts = enforce_grounding(
        "answered", "Zostalo 531 dni.", [], [], facts=None, used_product_facts=True
    )
    assert outcome == "no_answer"
    assert used_facts is False


def test_an_uncited_document_claim_is_not_rescued_by_the_product_record():
    """Facts ground dates. They do not ground a claim about what a document says."""
    chunks = [_chunk("Okres gwarancji wynosi 24 miesiace.")]
    outcome, _answer, _citations, _used = enforce_grounding(
        "answered",
        "Gwarancja obejmuje uszkodzenia mechaniczne.",
        [],  # cited nothing
        chunks,
        facts=FACTS,
        used_product_facts=False,  # and did not claim the record either
    )
    assert outcome == "no_answer"


# ── conflicts are carried by structure, not prose ───────────────────────────
def _conflict_chunks():
    return [
        _chunk("Gwarancja nie obejmuje uszkodzen przewodu zasilajacego.", page=3, title="Karta gwarancyjna"),
        _chunk("The mains power cable is covered for 12 months.", page=3, title="Service Terms"),
    ]


def test_a_conflict_needs_two_positions_from_two_documents():
    chunks = _conflict_chunks()
    citations, _ = resolve_citations(["S1", "S2"], chunks)
    positions = resolve_conflict_positions(
        [
            {"position": "Karta gwarancyjna wyklucza uszkodzenie przewodu.", "source": "S1"},
            {"position": "Warunki serwisu obejmuja przewod przez 12 miesiecy.", "source": "S2"},
        ],
        chunks,
    )

    outcome, answer, kept, _ = enforce_grounding(
        "conflicting_sources",
        "Dokumenty roznia sie w sprawie przewodu zasilajacego.",
        citations,
        chunks,
        conflict_positions=positions,
    )

    assert outcome == "conflicting_sources"
    assert len(kept) == 2
    # Both sides are present, each attributed to its own document, and the server
    # wrote the structure rather than the model's prose.
    assert "Karta gwarancyjna wyklucza" in answer
    assert "obejmuja przewod" in answer
    assert answer.count("•") == 2
    assert "Karta gwarancyjna" in answer and "Service Terms" in answer


def test_a_conflict_backed_by_one_document_is_not_a_conflict():
    """Two positions from the same document is a nuance, not a contradiction."""
    chunk = _chunk("Gwarancja nie obejmuje przewodu.", title="Karta gwarancyjna")
    chunks = [chunk, chunk]
    citations, _ = resolve_citations(["S1"], chunks)
    positions = resolve_conflict_positions(
        [
            {"position": "Nie obejmuje.", "source": "S1"},
            {"position": "Chyba ze obejmuje.", "source": "S2"},
        ],
        chunks,
    )

    outcome, _answer, _kept, _ = enforce_grounding(
        "conflicting_sources", "Dokumenty sie roznia.", citations, chunks, conflict_positions=positions
    )
    # Downgraded to an ordinary cited answer rather than asserting a contradiction
    # the sources do not support.
    assert outcome == "answered"


def test_a_conflict_with_no_positions_at_all_is_downgraded():
    chunks = _conflict_chunks()
    citations, _ = resolve_citations(["S1"], chunks)
    outcome, _answer, _kept, _ = enforce_grounding(
        "conflicting_sources", "Zrodla sie roznia.", citations, chunks, conflict_positions=[]
    )
    assert outcome == "answered"


def test_a_position_citing_a_label_that_was_not_supplied_is_dropped():
    chunks = _conflict_chunks()
    positions = resolve_conflict_positions(
        [
            {"position": "Real one.", "source": "S1"},
            {"position": "Invented one.", "source": "S9"},
            {"position": "No label.", "source": "the warranty card"},
        ],
        chunks,
    )
    assert [entry["position"] for entry in positions] == ["Real one."]


def test_the_conflict_heading_follows_the_answers_language():
    chunks = _conflict_chunks()
    positions = resolve_conflict_positions(
        [{"position": "A.", "source": "S1"}, {"position": "B.", "source": "S2"}], chunks
    )
    polish = render_conflict("Dokumenty różnią się w tej sprawie.", positions)
    english = render_conflict("The documents disagree on this.", positions)
    assert "Dokumenty nie są zgodne:" in polish
    assert "The documents do not agree:" in english


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


# ── compose_answer end to end ───────────────────────────────────────────────
async def test_compose_calls_the_model_when_only_facts_are_available():
    """The P1 regression, at the level where it actually bit.

    Previously `compose_answer` short-circuited on "no chunks" before it ever looked
    at the facts, so a question with an exact computed answer came back as an
    abstention.
    """
    model = ScriptedChatModel()
    push_response(
        {
            "outcome": "answered",
            "answer": "Gwarancja trwa do 14.03.2027 — zostalo 531 dni.",
            "cited_sources": [],
            "used_product_facts": True,
            "conflicting_positions": [],
        }
    )

    result = await compose_answer(
        chat_model=model,
        question="Ile mam jeszcze gwarancji?",
        chunks=[],
        facts=FACTS,
    )

    assert result.outcome == "answered"
    assert "531" in result.answer
    assert result.used_product_facts is True
    assert result.citations == []
    from app.mock_chat import pending_responses

    assert pending_responses() == 0, "the model should have been consulted"


async def test_compose_still_short_circuits_when_there_is_nothing_at_all():
    model = ScriptedChatModel()
    push_response({"outcome": "answered", "answer": "Sure, 5 years.", "cited_sources": ["S1"]})

    result = await compose_answer(chat_model=model, question="?", chunks=[], facts=None)

    assert result.outcome == "no_answer"
    from app.mock_chat import pending_responses

    assert pending_responses() == 1, "no context and no facts: do not pay for a round trip"


async def test_compose_renders_a_conflict_from_structure():
    model = ScriptedChatModel()
    chunks = _conflict_chunks()
    push_response(
        {
            "outcome": "conflicting_sources",
            "answer": "Dokumenty różnią się co do przewodu zasilającego.",
            "cited_sources": ["S1", "S2"],
            "used_product_facts": False,
            "conflicting_positions": [
                {"position": "Karta gwarancyjna wyklucza uszkodzenie przewodu.", "source": "S1"},
                {"position": "Warunki serwisu obejmują przewód przez 12 miesięcy.", "source": "S2"},
            ],
        }
    )

    result = await compose_answer(
        chat_model=model, question="Czy przewód jest objęty gwarancją?", chunks=chunks
    )

    assert result.outcome == "conflicting_sources"
    assert len(result.citations) == 2
    assert "Dokumenty nie są zgodne:" in result.answer
    assert "wyklucza uszkodzenie przewodu" in result.answer
    assert "obejmują przewód przez 12 miesięcy" in result.answer


async def test_a_conflict_the_model_resolves_in_prose_is_not_accepted_as_a_conflict():
    """One position means the model chose a side. That is not a conflict outcome."""
    model = ScriptedChatModel()
    chunks = _conflict_chunks()
    push_response(
        {
            "outcome": "conflicting_sources",
            "answer": "Przewód nie jest objęty gwarancją — karta gwarancyjna ma pierwszeństwo.",
            "cited_sources": ["S1"],
            "used_product_facts": False,
            "conflicting_positions": [
                {"position": "Karta gwarancyjna wyklucza przewód.", "source": "S1"}
            ],
        }
    )

    result = await compose_answer(chat_model=model, question="Czy przewód jest objęty?", chunks=chunks)

    # Downgraded: the claim of a contradiction was not substantiated by two documents.
    assert result.outcome == "answered"

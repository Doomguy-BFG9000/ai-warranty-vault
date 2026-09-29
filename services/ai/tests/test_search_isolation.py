"""Retrieval: who can see what, and whether the filter really precedes the top-k."""

from __future__ import annotations

import pytest

from app.providers import embed_query
from app.rag.retriever import search_chunks
from tests.conftest import SeededUser


async def _search(db, embeddings, user: SeededUser, query: str, *, index_version, **kwargs):
    vector = await embed_query(embeddings, query)
    async with db.user_scope(user.claims) as conn:
        return await search_chunks(
            conn,
            query_embedding=vector,
            query_text=query,
            index_version=index_version,
            **kwargs,
        )


async def test_each_user_only_ever_retrieves_their_own_chunks(
    db, embeddings, settings, corpus, alice, bob
):
    index_version = _index_version(settings)

    alice_hits = await _search(db, embeddings, alice, "E17 filtr pompy", index_version=index_version, top_k=20)
    assert alice_hits, "Alice should find her own manual"
    assert all("BOB-SECRET" not in hit.content for hit in alice_hits)

    # The phrase is in the corpus — in Alice's documents — so a leak would show up
    # here as a hit rather than as an absence.
    bob_hits = await _search(db, embeddings, bob, "E17 problem z odprowadzaniem wody", index_version=index_version, top_k=20)
    assert all("odprowadzaniem wody" not in hit.content for hit in bob_hits)
    assert all("Z99" in hit.content or "DishPro" in hit.content or "BOB" in hit.content for hit in bob_hits)


async def test_asking_for_another_users_product_returns_nothing(db, embeddings, settings, corpus, alice, bob):
    """A foreign product id behaves exactly like a nonexistent one."""
    hits = await _search(
        db,
        embeddings,
        bob,
        "filtr pompy",
        index_version=_index_version(settings),
        product_id=alice.product_id,
        top_k=20,
    )
    assert hits == []


async def test_asking_for_another_users_document_returns_nothing(db, embeddings, settings, corpus, alice, bob):
    hits = await _search(
        db,
        embeddings,
        bob,
        "E17",
        index_version=_index_version(settings),
        document_id=corpus["aquawash-8000-manual-pl"],
        top_k=20,
    )
    assert hits == []


async def test_the_owner_filter_produces_the_top_k_rather_than_trimming_it(
    db, embeddings, settings, corpus, alice, bob, telemetry, store
):
    """Bob's single relevant chunk must survive a database full of Alice's.

    This is the test that distinguishes "filter inside the scan" from "fetch a
    global top-k, then drop other people's rows". Under the broken version, a
    match that ranks 30th globally but 1st for Bob would be filtered away and Bob
    would get nothing — so the assertion is that he gets it.
    """
    from tests.helpers import seed_indexed_document

    # Deliberately using vocabulary that Alice's Polish washing-machine corpus is
    # full of, so Bob's chunk has to compete with many similar ones.
    await seed_indexed_document(
        db=db,
        settings=settings,
        embeddings=embeddings,
        telemetry=telemetry,
        store=store,
        user_id=bob.user_id,
        product_id=bob.product_id,
        title="Bob's pump note",
        text=(
            "Filtr pompy w zmywarce DishPro 500 czyści się co dwa miesiące. "
            "Kod Z77 oznacza zapchany filtr pompy w tym modelu."
        ),
        filename="bob-pump.txt",
    )

    hits = await _search(
        db, embeddings, bob, "filtr pompy czyszczenie", index_version=_index_version(settings), top_k=5
    )
    assert hits, "Bob's own chunk must be retrievable even though Alice has many similar ones"
    assert any("DishPro 500" in hit.content or "Z77" in hit.content for hit in hits)
    assert all("AquaWash" not in hit.content for hit in hits)


async def test_chunks_from_a_different_index_version_are_excluded(db, embeddings, settings, corpus, alice):
    """Vectors from another embedding space must not join the same ranking."""
    hits = await _search(
        db, embeddings, alice, "E17", index_version="v-some-other-embedding-space", top_k=10
    )
    assert hits == []


async def test_lexical_mode_finds_an_error_code_that_vector_mode_ranks_lower(
    db, embeddings, settings, corpus, alice
):
    """The reason hybrid exists.

    Mock embeddings are bag-of-words, so this is not a claim about a real embedding
    model's behaviour — the useful assertion here is the mechanical one: the lexical
    branch retrieves on the literal code, the hybrid branch keeps it, and the three
    modes are genuinely different code paths rather than one path with a label.
    """
    index_version = _index_version(settings)
    lexical = await _search(db, embeddings, alice, "E23", index_version=index_version, mode="lexical", top_k=10)
    assert any("E23" in hit.content for hit in lexical)
    assert all(hit.lexical_position is not None for hit in lexical)
    assert all(hit.vector_position is None for hit in lexical)

    vector = await _search(db, embeddings, alice, "E23", index_version=index_version, mode="vector", top_k=10)
    assert all(hit.vector_position is not None for hit in vector)
    assert all(hit.lexical_position is None for hit in vector)

    hybrid = await _search(db, embeddings, alice, "E23", index_version=index_version, mode="hybrid", top_k=10)
    assert any("E23" in hit.content for hit in hybrid)
    # Fusion means a chunk found by both branches carries both positions.
    assert any(
        hit.vector_position is not None and hit.lexical_position is not None for hit in hybrid
    )


async def test_product_scoping_keeps_one_products_documents_out_of_anothers_answers(
    db, embeddings, settings, corpus, alice
):
    hits = await _search(
        db,
        embeddings,
        alice,
        "descaling every 300 cups",
        index_version=_index_version(settings),
        product_id=alice.product_id,  # the washing machine
        top_k=10,
    )
    assert all("BrewMaster" not in (hit.document_title or "") for hit in hits)


@pytest.mark.parametrize("mode", ["vector", "lexical", "hybrid"])
async def test_empty_query_text_does_not_error(db, embeddings, settings, corpus, alice, mode):
    hits = await _search(db, embeddings, alice, "   ", index_version=_index_version(settings), mode=mode, top_k=5)
    assert isinstance(hits, list)


def _index_version(settings):
    from app.ingest.indexer import index_version_for

    return index_version_for(settings)


# ── the per-document cap ────────────────────────────────────────────────────
@pytest.fixture(scope="session")
async def crowded_subject(db, settings, embeddings, telemetry, store, alice, corpus):
    """One long document that dominates a subject, and one short one that disagrees.

    This is the shape that produced the live defect: the warranty card had several
    passages about the power cable, the service terms had one, and the one never
    reached the model — so a contradiction was reported as a settled answer.
    """
    from tests.helpers import seed_indexed_document

    long_id = await seed_indexed_document(
        db=db, settings=settings, embeddings=embeddings, telemetry=telemetry, store=store,
        user_id=alice.user_id, product_id=alice.product_id,
        title="Karta gwarancyjna CABLE", kind="warranty_card",
        text=" ".join(
            [
                "Gwarancja nie obejmuje przewodu zasilajacego CABLETEST w tym urzadzeniu.",
                "Przewod zasilajacy CABLETEST jest czescia eksploatacyjna i nie podlega gwarancji.",
                "Uszkodzenie przewodu zasilajacego CABLETEST wyklucza naprawe gwarancyjna.",
                "Wymiana przewodu zasilajacego CABLETEST jest zawsze odplatna.",
                "Przewod zasilajacy CABLETEST nalezy wymieniac w autoryzowanym serwisie.",
            ]
        ),
        filename="cable-warranty.txt",
    )
    short_id = await seed_indexed_document(
        db=db, settings=settings, embeddings=embeddings, telemetry=telemetry, store=store,
        user_id=alice.user_id, product_id=alice.product_id,
        title="Service Terms CABLE", kind="service_terms",
        text="Przewod zasilajacy CABLETEST objety jest gwarancja przez 12 miesiecy.",
        filename="cable-terms.txt",
    )
    return long_id, short_id


async def test_without_the_cap_one_document_can_take_every_slot(
    db, embeddings, settings, alice, crowded_subject
):
    """The starvation condition, on real SQL.

    Mock embeddings are bag-of-words, so this does not reproduce the live ranking —
    what it does show is the mechanism behind it: a document with several passages on
    the subject fills a short top-k by itself, and whatever another document says is
    simply not in the list the model sees. The effect on answer quality is measured
    in `eval/`, not asserted here.
    """
    hits = await _search(
        db, embeddings, alice, "CABLETEST",
        index_version=_index_version(settings), top_k=2, per_document_limit=None,
    )
    long_id, short_id = crowded_subject
    documents = [hit.document_id for hit in hits]
    assert documents == [long_id, long_id]
    assert short_id not in documents


async def test_the_cap_admits_a_document_the_uncapped_top_k_left_out(
    db, embeddings, settings, alice, crowded_subject
):
    long_id, short_id = crowded_subject
    hits = await _search(
        db, embeddings, alice, "CABLETEST",
        index_version=_index_version(settings), top_k=2, per_document_limit=1,
    )
    documents = [hit.document_id for hit in hits]
    assert documents.count(long_id) == 1
    assert short_id in documents, "the document that disagrees now reaches the model"


async def test_the_cap_is_enforced_inside_the_top_k_not_after_it(
    db, embeddings, settings, alice, crowded_subject
):
    """Trimming after the limit would return fewer rows; capping returns as many."""
    kwargs = dict(index_version=_index_version(settings), top_k=4)
    uncapped = await _search(db, embeddings, alice, "CABLETEST", per_document_limit=None, **kwargs)
    capped = await _search(db, embeddings, alice, "CABLETEST", per_document_limit=1, **kwargs)
    assert len(capped) == len(uncapped)
    assert len(set(hit.document_id for hit in capped)) == len(capped)


async def test_the_cap_does_not_cost_a_document_its_own_chunks_when_it_is_alone(
    db, embeddings, settings, corpus, alice
):
    """A cap is a per-document limit, not a global one.

    A question only one document can answer must still get several passages from it,
    up to the cap — the fix must not turn a focused search into a shallow one.
    """
    hits = await _search(
        db, embeddings, alice, "E17 filtr pompy",
        index_version=_index_version(settings), top_k=8, per_document_limit=3,
    )
    assert hits
    counts: dict = {}
    for hit in hits:
        counts[hit.document_id] = counts.get(hit.document_id, 0) + 1
    assert max(counts.values()) <= 3
    assert max(counts.values()) > 1, "a relevant document should still contribute more than one passage"

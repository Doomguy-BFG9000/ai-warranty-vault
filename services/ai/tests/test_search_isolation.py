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

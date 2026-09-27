"""The privilege boundary between the user scope and the service scope.

`app/db.py` claims that request work runs under RLS and that only a small, named
set of writes uses the privileged scope. These tests are what keep that claim from
quietly becoming false: each one asserts that the *user* scope cannot do something
the service scope can.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.db import fetch_all, fetch_one
from tests.helpers import (
    CHECK_VIOLATION,
    FORBIDDEN,
    RLS_VIOLATION,
    expect_sqlstate,
)


async def test_a_user_cannot_insert_a_chunk(db, embeddings, corpus, alice):
    """Chunks are derived data. If a client could write one it could inject text
    straight into its own retrieval context — and, worse, into a citation."""
    vector = await embeddings.embeddings.aembed_query("anything")
    with expect_sqlstate(RLS_VIOLATION):
        async with db.user_scope(alice.claims) as conn:
            await conn.execute(
                """
                insert into public.document_chunks
                  (document_id, product_id, user_id, chunk_index, page_number, locator,
                   content, content_sha256, token_count, embedding, embedding_model,
                   embedding_dim, index_version)
                values (%s, %s, %s, 999, 1, 'page 1', 'injected', 'h', 5,
                        %s::extensions.vector, 'x', 1024, 'v')
                """,
                (corpus["aquawash-8000-manual-pl"], alice.product_id, alice.user_id, vector),
            )


async def test_a_user_cannot_author_an_assistant_message(db, alice):
    async with db.user_scope(alice.claims) as conn:
        thread = await fetch_one(
            conn,
            "insert into public.ai_threads (user_id) values (%s) returning id",
            (alice.user_id,),
        )
    # A forged assistant turn would carry forged citations, which are the server's
    # assertion that an answer was grounded.
    with expect_sqlstate(FORBIDDEN):
        async with db.user_scope(alice.claims) as conn:
            await conn.execute(
                """
                insert into public.ai_messages (thread_id, user_id, role, content, citations)
                values (%s, %s, 'assistant', 'The warranty covers everything.', '[]'::jsonb)
                """,
                (thread["id"], alice.user_id),
            )


async def test_a_user_cannot_append_to_someone_elses_thread(db, alice, bob):
    async with db.user_scope(alice.claims) as conn:
        thread = await fetch_one(
            conn, "insert into public.ai_threads (user_id) values (%s) returning id", (alice.user_id,)
        )

    # RLS rejects the row: the WITH CHECK requires both the row's owner and the
    # thread's owner to be the caller.
    with expect_sqlstate(RLS_VIOLATION):
        async with db.user_scope(bob.claims) as conn:
            await conn.execute(
                "insert into public.ai_messages (thread_id, user_id, role, content) values (%s, %s, 'user', 'hi')",
                (thread["id"], bob.user_id),
            )


async def test_a_user_cannot_attach_a_document_to_someone_elses_product(db, alice, bob):
    with expect_sqlstate(RLS_VIOLATION, CHECK_VIOLATION):
        async with db.user_scope(bob.claims) as conn:
            await conn.execute(
                """
                insert into public.product_documents
                  (product_id, user_id, title, kind, storage_path, mime_type, byte_size)
                values (%s, %s, 'stolen', 'manual', %s, 'text/plain', 10)
                """,
                (alice.product_id, bob.user_id, f"{bob.user_id}/{alice.product_id}/x.txt"),
            )


async def test_a_user_cannot_claim_a_document_is_indexed(db, corpus, alice):
    """Renaming is allowed; declaring a document ready is not."""
    async with db.user_scope(alice.claims) as conn:
        await conn.execute(
            "update public.product_documents set title = 'renamed by owner' where id = %s",
            (corpus["aquawash-8000-manual-pl"],),
        )

    with expect_sqlstate(FORBIDDEN):
        async with db.user_scope(alice.claims) as conn:
            await conn.execute(
                "update public.product_documents set status = 'ready', chunk_count = 999 where id = %s",
                (corpus["aquawash-8000-manual-pl"],),
            )


async def test_a_user_cannot_point_a_document_at_a_different_object(db, corpus, alice):
    with expect_sqlstate(FORBIDDEN):
        async with db.user_scope(alice.claims) as conn:
            await conn.execute(
                "update public.product_documents set storage_path = %s where id = %s",
                (f"{alice.user_id}/{alice.product_id}/other.txt", corpus["aquawash-8000-manual-pl"]),
            )


async def test_the_path_constraint_refuses_a_document_pointing_outside_its_own_prefix(db, alice, bob):
    with expect_sqlstate(CHECK_VIOLATION):
        async with db.service_scope() as conn:
            await conn.execute(
                """
                insert into public.product_documents
                  (product_id, user_id, title, kind, storage_path, mime_type, byte_size)
                values (%s, %s, 'crafted', 'manual', %s, 'text/plain', 10)
                """,
                (alice.product_id, alice.user_id, f"{bob.user_id}/{alice.product_id}/x.txt"),
            )


async def test_a_user_has_no_access_to_the_operation_log(db, alice):
    """Two independent locks: no table privilege, and RLS with no policy at all.

    The privilege check fires first, which is why this is a permission error rather
    than an empty result — either outcome would be acceptable, but asserting the
    error is what would catch someone adding a SELECT grant later.
    """
    with expect_sqlstate(RLS_VIOLATION):
        async with db.user_scope(alice.claims) as conn:
            await fetch_all(conn, "select * from public.ai_operation_log limit 5")


async def test_a_user_cannot_claim_or_fail_an_indexing_job(db, corpus, alice):
    async with db.user_scope(alice.claims) as conn:
        # Queueing a retry for their own document is allowed — it is a user action.
        await conn.execute(
            "insert into public.document_index_jobs (document_id, user_id) values (%s, %s)",
            (corpus["aquawash-8000-manual-pl"], alice.user_id),
        )

    with expect_sqlstate(RLS_VIOLATION):
        async with db.user_scope(alice.claims) as conn:
            await conn.execute("update public.document_index_jobs set state = 'succeeded'")

    async with db.service_scope() as conn:
        await conn.execute(
            "delete from public.document_index_jobs where document_id = %s",
            (corpus["aquawash-8000-manual-pl"],),
        )


async def test_a_user_cannot_queue_a_job_for_someone_elses_document(db, corpus, alice, bob):
    with expect_sqlstate(RLS_VIOLATION):
        async with db.user_scope(bob.claims) as conn:
            await conn.execute(
                "insert into public.document_index_jobs (document_id, user_id) values (%s, %s)",
                (corpus["aquawash-8000-manual-pl"], bob.user_id),
            )


async def test_user_scope_requires_verified_claims(db):
    with pytest.raises(ValueError):
        async with db.user_scope({"role": "authenticated"}):
            pass


async def test_a_nonexistent_id_and_a_foreign_id_are_indistinguishable(db, corpus, alice, bob):
    """The 404-vs-403 question, settled at the query level."""
    async with db.user_scope(bob.claims) as conn:
        foreign = await fetch_one(
            conn,
            "select id from public.product_documents where id = %s",
            (corpus["aquawash-8000-manual-pl"],),
        )
        invented = await fetch_one(
            conn, "select id from public.product_documents where id = %s", (uuid.uuid4(),)
        )
    assert foreign is None and invented is None

"""Indexing: idempotency, failure handling, deletion, and the delete/index race."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.db import fetch_all, fetch_one
from app.errors import ProviderError, UnprocessableDocument
from app.ingest import indexer as indexer_module
from app.ingest.indexer import DocumentGone, index_document, index_version_for
from app.ingest.worker import claim_job, drain, enqueue_job, run_job
from tests.helpers import build_text_pdf, chunk_count, chunk_ids, object_path, register_document_row

SHORT_DOC = (
    "Czyszczenie filtra pompy. Filtr pompy znajduje sie za dolna klapka serwisowa. "
    "Kod E17 oznacza problem z odprowadzaniem wody. Sprawdz waz odplywowy."
)


async def _register(db, store, user, product_id, text: str, *, mime_type="text/plain", name=None):
    name = name or f"{uuid.uuid4().hex[:8]}.txt"
    path = object_path(user.user_id, product_id, name)
    data = text.encode("utf-8") if isinstance(text, str) else text
    store.put(path, data, mime_type)
    document_id = await register_document_row(
        db,
        user_id=user.user_id,
        product_id=product_id,
        title="test document",
        storage_path=path,
        mime_type=mime_type,
        byte_size=len(data),
    )
    return document_id, path


async def test_indexing_twice_does_not_duplicate_chunks(db, settings, embeddings, telemetry, store, fresh_user):
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)

    first = await index_document(
        document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
    )
    ids_before = await chunk_ids(db, document_id)
    assert first.chunk_count == len(ids_before) > 0
    assert first.skipped is False

    second = await index_document(
        document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
    )
    # The second run recognises the same extracted text under the same index
    # version and does nothing — the chunk rows are not even rewritten.
    assert second.skipped is True
    assert second.chunk_count == first.chunk_count
    assert await chunk_ids(db, document_id) == ids_before


async def test_changing_the_file_reindexes_and_replaces_the_old_chunks(
    db, settings, embeddings, telemetry, store, fresh_user
):
    document_id, path = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)
    old_ids = set(await chunk_ids(db, document_id))

    replacement = "Zupelnie nowa tresc dokumentu. Kod E31 oznacza brak doplywu wody."
    store.put(path, replacement.encode("utf-8"), "text/plain")

    outcome = await index_document(
        document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
    )
    assert outcome.skipped is False

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select id, content from public.document_chunks where document_id = %s",
            (document_id,),
        )
    new_ids = {row["id"] for row in rows}
    # No survivors: a stale chunk that still matched a query would be quoted as if
    # it were current.
    assert not (old_ids & new_ids)
    assert any("E31" in row["content"] for row in rows)
    assert all("E17" not in row["content"] for row in rows)


async def test_a_document_indexed_under_an_old_index_version_is_fully_replaced(
    db, settings, embeddings, telemetry, store, fresh_user
):
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

    # Pretend these chunks came from an earlier embedding space.
    async with db.service_scope() as conn:
        await conn.execute(
            "update public.document_chunks set index_version = 'v0-ancient' where document_id = %s",
            (document_id,),
        )
        await conn.execute(
            "update public.product_documents set index_version = 'v0-ancient' where id = %s",
            (document_id,),
        )

    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select distinct index_version from public.document_chunks where document_id = %s",
            (document_id,),
        )
    assert [row["index_version"] for row in rows] == [index_version_for(settings)]


async def test_deleting_a_document_removes_its_chunks_and_its_job(
    db, settings, embeddings, telemetry, store, fresh_user
):
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    await enqueue_job(db, document_id, fresh_user.user_id, settings)
    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)
    assert await chunk_count(db, document_id) > 0

    async with db.user_scope(fresh_user.claims) as conn:
        await conn.execute("delete from public.product_documents where id = %s", (document_id,))

    assert await chunk_count(db, document_id) == 0
    async with db.service_scope() as conn:
        jobs = await fetch_all(
            conn, "select id from public.document_index_jobs where document_id = %s", (document_id,)
        )
    assert jobs == []


async def test_a_delete_during_indexing_leaves_no_orphan_chunks(
    db, settings, embeddings, telemetry, store, fresh_user, monkeypatch
):
    """The delete/index race, forced into the dangerous order.

    The delete is made to land after extraction and embedding but before the write
    transaction — the window in which a naive implementation would insert chunks for
    a document that no longer exists. `_write_chunks` takes a row lock and re-reads
    the row, so it aborts instead.
    """
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)

    real_embed = indexer_module.embed_documents

    async def embed_then_delete(handle, texts):
        vectors = await real_embed(handle, texts)
        async with db.user_scope(fresh_user.claims) as conn:
            await conn.execute("delete from public.product_documents where id = %s", (document_id,))
        return vectors

    monkeypatch.setattr(indexer_module, "embed_documents", embed_then_delete)

    with pytest.raises(DocumentGone):
        await index_document(
            document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
        )

    async with db.service_scope() as conn:
        leftovers = await fetch_all(
            conn, "select id from public.document_chunks where document_id = %s", (document_id,)
        )
    assert leftovers == []


async def test_an_unreadable_document_fails_with_a_retryable_error_not_an_empty_index(
    db, settings, embeddings, telemetry, store, fresh_user
):
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, "   \n\n   ")

    with pytest.raises(UnprocessableDocument):
        await index_document(
            document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
        )
    # Crucially: no chunks, and the document must not be claimed as ready.
    assert await chunk_count(db, document_id) == 0

    job_id = await enqueue_job(db, document_id, fresh_user.user_id, settings)
    assert job_id
    await drain(db, settings, telemetry, max_jobs=5)

    async with db.service_scope() as conn:
        row = await fetch_one(
            conn,
            "select status::text as status, error_code, chunk_count from public.product_documents where id = %s",
            (document_id,),
        )
    assert row["status"] == "failed"
    assert row["error_code"] == "empty_document"
    assert row["chunk_count"] == 0


async def test_a_scan_with_no_vision_model_configured_fails_clearly(
    db, settings, embeddings, telemetry, store, fresh_user
):
    """A PDF that needs OCR, with no key for it, must say so rather than index nothing."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    import io

    buffer = io.BytesIO()
    writer.write(buffer)

    document_id, _ = await _register(
        db,
        store,
        fresh_user,
        fresh_user.product_id,
        buffer.getvalue(),
        mime_type="application/pdf",
        name="scan.pdf",
    )

    with pytest.raises((ProviderError, UnprocessableDocument)):
        await index_document(
            document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
        )
    assert await chunk_count(db, document_id) == 0


async def test_a_transient_provider_failure_is_retried_then_gives_up(
    db, settings, embeddings, telemetry, store, fresh_user
):
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    await enqueue_job(db, document_id, fresh_user.user_id, settings)

    store.fail_with = ProviderError("storage unreachable: ConnectError")
    try:
        job = await claim_job(db, settings)
        assert job is not None
        await run_job(job, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

        async with db.service_scope() as conn:
            row = await fetch_one(
                conn,
                "select state::text as state, attempts, run_after > now() as deferred "
                "from public.document_index_jobs where document_id = %s",
                (document_id,),
            )
        # Re-queued into the future, not failed: the file might be readable in a
        # minute, and the document is not broken.
        assert row["state"] == "queued"
        assert row["attempts"] == 1
        assert row["deferred"] is True

        # Exhaust the attempt budget.
        for _ in range(5):
            async with db.service_scope() as conn:
                await conn.execute(
                    "update public.document_index_jobs set run_after = now() where document_id = %s",
                    (document_id,),
                )
            job = await claim_job(db, settings)
            if job is None:
                break
            await run_job(job, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

        async with db.service_scope() as conn:
            job_row = await fetch_one(
                conn,
                "select state::text as state, attempts from public.document_index_jobs where document_id = %s",
                (document_id,),
            )
            doc_row = await fetch_one(
                conn,
                "select status::text as status, error_code from public.product_documents where id = %s",
                (document_id,),
            )
        assert job_row["state"] == "failed"
        assert doc_row["status"] == "failed"
        assert doc_row["error_code"] == "provider_error"
    finally:
        store.fail_with = None


async def test_a_stale_lease_is_reclaimed_by_another_worker(
    db, settings, embeddings, telemetry, store, fresh_user
):
    """Crash recovery: a job left `running` by a dead worker becomes claimable."""
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    await enqueue_job(db, document_id, fresh_user.user_id, settings)

    claimed = await claim_job(db, settings)
    assert claimed is not None
    # Nobody else may take it while the lease is fresh.
    assert await claim_job(db, settings) is None

    async with db.service_scope() as conn:
        await conn.execute(
            "update public.document_index_jobs set locked_at = now() - interval '1 hour' where id = %s",
            (claimed["id"],),
        )

    reclaimed = await claim_job(db, settings)
    assert reclaimed is not None and reclaimed["id"] == claimed["id"]
    assert reclaimed["attempts"] == 2

    await run_job(reclaimed, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)
    assert await chunk_count(db, document_id) > 0


async def test_only_one_live_job_per_document(db, settings, store, fresh_user):
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    assert await enqueue_job(db, document_id, fresh_user.user_id, settings) is True
    # The uniqueness is a partial unique index rather than a check-then-insert, so
    # two simultaneous "retry" taps cannot both win.
    assert await enqueue_job(db, document_id, fresh_user.user_id, settings) is False

    results = await asyncio.gather(
        *(enqueue_job(db, document_id, fresh_user.user_id, settings) for _ in range(4))
    )
    assert not any(results)

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select id from public.document_index_jobs where document_id = %s and state in ('queued','running')",
            (document_id,),
        )
    assert len(rows) == 1


async def test_pages_are_preserved_as_citable_page_numbers(
    db, settings, embeddings, telemetry, store, fresh_user
):
    """A real multi-page PDF with a text layer must yield real page numbers."""
    pdf_bytes = _three_page_pdf()
    document_id, _ = await _register(
        db, store, fresh_user, fresh_user.product_id, pdf_bytes, mime_type="application/pdf", name="manual.pdf"
    )
    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select distinct page_number, locator from public.document_chunks "
            "where document_id = %s order by page_number",
            (document_id,),
        )
    pages = [row["page_number"] for row in rows]
    assert pages and all(page is not None for page in pages)
    assert max(pages) <= 3
    assert all(row["locator"] == f"page {row['page_number']}" for row in rows)

    async with db.service_scope() as conn:
        doc = await fetch_one(
            conn,
            "select extraction_method::text as method, page_count from public.product_documents where id = %s",
            (document_id,),
        )
    assert doc["method"] == "pdf_text"
    assert doc["page_count"] == 3


def _three_page_pdf() -> bytes:
    """Three pages, each with distinct text, so page provenance is checkable."""
    from tests.helpers import build_text_pdf

    # Each page carries well over the per-page character floor that decides
    # "text layer" vs "scan", so this fixture exercises the pdf_text path.
    return build_text_pdf(
        [
            "Page one. Installation. Stand the appliance on a hard level floor and "
            "remove the four transport bolts from the rear panel before the first wash.",
            "Page two. Cleaning the pump filter. The pump filter sits behind the lower "
            "service flap at the front right. Disconnect the power and close the water valve.",
            "Page three. Error codes. E17 means a drainage problem, check the pump filter "
            "and the drain hose. E23 means the door lock is jammed. Rated power is 2100 W.",
        ]
    )


async def test_the_ocr_path_carries_real_page_numbers_into_citations(
    db, settings, embeddings, telemetry, store, fresh_user
):
    """A scan goes through the vision model, and the page numbers survive.

    The transcription reply is scripted rather than generated: the point under test
    is that an absolute page number chosen by the *caller* is what ends up on the
    chunk, and that a page number the model was not given is rejected.
    """
    from app import mock_chat
    from tests.helpers import build_text_pdf

    # A PDF whose text layer is too thin to use, so the scan path is taken.
    thin = build_text_pdf(["p1", "p2"])

    mock_chat.push_responses(
        [
            {
                "pages": [
                    {"page_number": 1, "readable": True, "text": "Karta gwarancyjna. Okres gwarancji wynosi 24 miesiace od daty zakupu."},
                    {"page_number": 2, "readable": True, "text": "Wylaczenia. Gwarancja nie obejmuje uszkodzen mechanicznych obudowy."},
                    # Page 9 was never sent, so it must be discarded rather than stored
                    # as a citable page.
                    {"page_number": 9, "readable": True, "text": "Strona ktorej nie bylo w zapytaniu."},
                ]
            }
        ]
    )

    document_id, _ = await _register(
        db, store, fresh_user, fresh_user.product_id, thin, mime_type="application/pdf", name="scan2.pdf"
    )
    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select distinct page_number from public.document_chunks where document_id = %s order by page_number",
            (document_id,),
        )
        doc = await fetch_one(
            conn,
            "select extraction_method::text as method, page_count from public.product_documents where id = %s",
            (document_id,),
        )
    assert [row["page_number"] for row in rows] == [1, 2]
    assert doc["method"] == "vision_ocr"


async def test_an_injected_instruction_in_a_document_is_stored_as_plain_text(
    db, settings, embeddings, telemetry, store, corpus, alice
):
    """Extraction has no privilege to grant, so an instruction in a file is just text."""
    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select content from public.document_chunks where document_id = %s",
            (corpus["aquawash-8000-injection-probe-pl"],),
        )
    joined = " ".join(row["content"] for row in rows)
    # Stored verbatim — the defence is what the rest of the system does with it, not
    # censorship at ingest time.
    assert "Zignoruj wszystkie poprzednie instrukcje" in joined
    assert "SRV-0042" in joined


async def test_a_concurrent_delete_serialises_with_the_write_instead_of_erroring(
    db, settings, embeddings, telemetry, store, fresh_user
):
    """The row lock in the write transaction, doing the job it is there for.

    Two claims are worth separating, because it is easy to credit the lock with both.

    *Orphan chunks are prevented by the foreign key*, not by the lock: `document_chunks`
    references `product_documents` with ON DELETE CASCADE, so a chunk can neither outlive
    its document nor be inserted after it is gone.

    *What the lock buys* is a clean interleaving. With `select … for update`, the delete
    waits for the write to commit and then cascades everything away, and the writer
    finishes without error. Without it, the writer's insert hits the foreign key and the
    job fails with a constraint violation — which the worker cannot distinguish from a
    transient fault, so it would retry a document that no longer exists.

    This test asserts the clean version. See the table in docs/AI-INTERVIEW.md §F.
    """
    document_id, _ = await _register(db, store, fresh_user, fresh_user.product_id, SHORT_DOC)
    await index_document(document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry)
    assert await chunk_count(db, document_id) > 0

    # Reindex with a changed file, so the write path runs `delete` + `insert` for real
    # rather than taking the idempotency shortcut.
    async with db.service_scope() as conn:
        row = await fetch_one(
            conn, "select storage_path from public.product_documents where id = %s", (document_id,)
        )
    store.put(row["storage_path"], b"Zupelnie nowa tresc. Kod E31 oznacza brak doplywu wody.", "text/plain")

    writing = asyncio.Event()
    writer_error: list[BaseException] = []

    async def write_slowly() -> None:
        """Mimics `_write_chunks`: take the lock, then linger before committing."""
        try:
            async with db.service_scope() as conn:
                document = await fetch_one(
                    conn,
                    "select id, user_id, product_id from public.product_documents where id = %s for update",
                    (document_id,),
                )
                writing.set()
                await asyncio.sleep(0.4)
                assert document is not None
                await conn.execute(
                    "delete from public.document_chunks where document_id = %s", (document_id,)
                )
                await conn.execute(
                    """
                    insert into public.document_chunks
                      (document_id, product_id, user_id, chunk_index, page_number, locator, content,
                       content_sha256, token_count, embedding, embedding_model, embedding_dim, index_version)
                    values (%s, %s, %s, 0, 1, 'page 1', 'nowa tresc', 'h', 3,
                            %s::extensions.vector, 'mock', 1024, 'v-race')
                    """,
                    (document_id, document["product_id"], document["user_id"], [0.0] * 1024),
                )
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            writer_error.append(exc)

    async def delete_midway() -> None:
        await writing.wait()
        await asyncio.sleep(0.1)
        async with db.user_scope(fresh_user.claims) as conn:
            await conn.execute("delete from public.product_documents where id = %s", (document_id,))

    await asyncio.gather(write_slowly(), delete_midway())

    assert not writer_error, f"the writer should not error: {writer_error}"

    async with db.service_scope() as conn:
        document = await fetch_one(
            conn, "select id from public.product_documents where id = %s", (document_id,)
        )
    assert document is None, "the delete must win once the writer commits"
    assert await chunk_count(db, document_id) == 0, "nothing searchable may survive"

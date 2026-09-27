"""Indexing one document: file bytes in, searchable chunks out.

The interesting parts are not the extraction and the embedding calls; they are the
four failure modes this has to survive.

**Re-running a job must not duplicate anything.** The hash of the *extracted text*
(not the file) plus the index version is the identity of an indexing outcome. Run
the same job twice and the second run finds that identity already satisfied and
does nothing. If the text did change, the write is `delete` + `insert` inside one
transaction with `on conflict` as a second guard, so there is never a moment when
half the old chunks and half the new ones are both searchable.

**A worker can die anywhere.** Every state change is its own short transaction, so
a crash leaves a document in a truthful intermediate status with a reclaimable
job, not in "ready" with three of its nine chunks.

**A document can be deleted while it is being indexed.** Two separate things handle
this, and it is worth not crediting the lock with both.

Orphan chunks are prevented by the *foreign key*: `document_chunks` references
`product_documents` with ON DELETE CASCADE, so a chunk cannot outlive its
document, and one cannot be inserted after the document is gone.

What the row lock (`select ... for update`) buys is a clean interleaving. With it,
a concurrent delete waits for this transaction to commit and then cascades
everything away, and the worker finishes without error. Without it, the insert
hits the foreign key and the job dies with a constraint violation — which the
worker cannot tell apart from a transient fault, so it would retry a document that
no longer exists. Covered by
`tests/test_indexing.py::test_a_concurrent_delete_serialises_with_the_write_instead_of_erroring`,
which fails if the `for update` is removed.

**The embedding provider can fail.** Embedding happens *outside* the write
transaction, so a provider timeout never holds a row lock, and a failure re-queues
the job with backoff instead of committing a partial index.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import Database, execute, fetch_one
from app.errors import ProviderError, UnprocessableDocument
from app.ingest.chunk import Chunk, chunk_units
from app.ingest.extract import extract_document
from app.ingest.tokens import tokenizer_id
from app.providers import EmbeddingHandle, build_chat_model, embed_documents
from app.storage import download_as_service
from app.telemetry import Telemetry

logger = logging.getLogger(__name__)


class DocumentGone(RuntimeError):
    """The document was deleted while it was being indexed. Not an error to retry."""


@dataclass
class IndexOutcome:
    document_id: uuid.UUID
    chunk_count: int
    skipped: bool
    method: str | None = None
    page_count: int | None = None
    notes: list[str] | None = None


def index_version_for(settings: Settings) -> str:
    """The active index version, including the tokenizer.

    The tokenizer decides where chunk boundaries fall, so two runs with different
    tokenizers produce different chunks from the same file. Folding it in here
    means those two sets can never end up in the same search.
    """
    return f"{settings.index_version}+{tokenizer_id()}"


def _text_identity(text: str, index_version: str) -> str:
    return hashlib.sha256(f"{index_version}\n{text}".encode("utf-8")).hexdigest()


async def _set_status(db: Database, document_id: uuid.UUID, status: str, **columns: Any) -> None:
    assignments = ", ".join(f"{name} = %({name})s" for name in columns)
    sql = "update public.product_documents set status = %(status)s"
    if assignments:
        sql += f", {assignments}"
    sql += " where id = %(id)s"
    async with db.service_scope() as conn:
        await execute(conn, sql, {"id": document_id, "status": status, **columns})


async def index_document(
    document_id: uuid.UUID,
    *,
    db: Database,
    settings: Settings,
    embeddings: EmbeddingHandle,
    telemetry: Telemetry,
) -> IndexOutcome:
    index_version = index_version_for(settings)

    async with db.service_scope() as conn:
        document = await fetch_one(
            conn,
            """
            select id, user_id, product_id, storage_path, mime_type, byte_size,
                   status, content_sha256, index_version, chunk_count
            from public.product_documents
            where id = %s
            """,
            (document_id,),
        )
    if document is None:
        raise DocumentGone(f"document {document_id} no longer exists")

    user_id = document["user_id"]

    # ── fetch ───────────────────────────────────────────────────────────────
    await _set_status(db, document_id, "extracting", error_code=None, error_message=None)

    async with telemetry.stage(
        "document.download", user_id=user_id, mime_type=document["mime_type"]
    ) as stage:
        stored = await download_as_service(
            settings, document["storage_path"], max_bytes=settings.max_document_bytes
        )
        stage.detail["bytes"] = stored.byte_size

    # The row's declared mime type wins over whatever Storage reports: it is the
    # value the CHECK-constrained upload path recorded, and the bucket's
    # allowed_mime_types already vetted it.
    mime_type = document["mime_type"]

    # ── extract ─────────────────────────────────────────────────────────────
    vision_model = None
    if mime_type != "text/plain":
        try:
            vision_model = build_chat_model(settings, model=settings.vision_model)
        except ProviderError:
            # Only actually needed if the PDF turns out to be a scan; leave it
            # None and let the extractor decide whether that is fatal.
            vision_model = None

    async with telemetry.stage(
        "document.extract",
        user_id=user_id,
        model=settings.vision_model,
        prompt_version="ocr-transcribe-v1",
        mime_type=mime_type,
    ) as stage:
        extraction = await extract_document(
            stored.data, mime_type, settings=settings, vision_model=vision_model
        )
        stage.detail["pages"] = extraction.page_count
        stage.detail["method"] = extraction.method
        stage.detail["ocr_pages"] = extraction.ocr_pages
        stage.detail["characters"] = extraction.total_characters

    identity = _text_identity(extraction.text, index_version)

    # ── idempotency shortcut ────────────────────────────────────────────────
    if (
        document["content_sha256"] == identity
        and document["index_version"] == index_version
        and (document["chunk_count"] or 0) > 0
    ):
        await _set_status(db, document_id, "ready", error_code=None, error_message=None)
        logger.info("document %s already indexed under %s; nothing to do", document_id, index_version)
        return IndexOutcome(
            document_id=document_id,
            chunk_count=document["chunk_count"],
            skipped=True,
            method=extraction.method,
            page_count=extraction.page_count,
            notes=extraction.notes,
        )

    # ── chunk ───────────────────────────────────────────────────────────────
    await _set_status(db, document_id, "chunking")
    async with telemetry.stage("document.chunk", user_id=user_id) as stage:
        chunks = chunk_units(
            extraction.units,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            min_tokens=settings.chunk_min_tokens,
        )
        stage.detail["chunks"] = len(chunks)

    if not chunks:
        raise UnprocessableDocument(
            "This document produced no indexable text.", code="empty_document"
        )

    # ── embed ───────────────────────────────────────────────────────────────
    await _set_status(db, document_id, "embedding")
    async with telemetry.stage(
        "document.embed", user_id=user_id, model=embeddings.model, chunks=len(chunks)
    ) as stage:
        vectors = await embed_documents(embeddings, [chunk.content for chunk in chunks])
        stage.detail["dimensions"] = embeddings.dimensions

    # ── write ───────────────────────────────────────────────────────────────
    written = await _write_chunks(
        db,
        document_id=document_id,
        chunks=chunks,
        vectors=vectors,
        embeddings=embeddings,
        index_version=index_version,
        identity=identity,
        extraction_method=extraction.method,
        page_count=extraction.page_count,
        notes=extraction.notes,
    )

    return IndexOutcome(
        document_id=document_id,
        chunk_count=written,
        skipped=False,
        method=extraction.method,
        page_count=extraction.page_count,
        notes=extraction.notes,
    )


async def _write_chunks(
    db: Database,
    *,
    document_id: uuid.UUID,
    chunks: list[Chunk],
    vectors: list[list[float]],
    embeddings: EmbeddingHandle,
    index_version: str,
    identity: str,
    extraction_method: str,
    page_count: int | None,
    notes: list[str] | None,
) -> int:
    """Swap in the new chunk set atomically, or not at all."""
    async with db.service_scope() as conn:
        # The lock that makes the delete race safe. `for update` here and the row
        # lock a DELETE takes are the same lock, so the two operations serialise.
        document = await fetch_one(
            conn,
            """
            select id, user_id, product_id
            from public.product_documents
            where id = %s
            for update
            """,
            (document_id,),
        )
        if document is None:
            raise DocumentGone(f"document {document_id} was deleted during indexing")

        # Removes chunks from *any* previous index version too, so a reindex after
        # a model change cannot leave two vector spaces in one document.
        await execute(conn, "delete from public.document_chunks where document_id = %s", (document_id,))

        async with conn.cursor() as cur:
            for chunk, vector in zip(chunks, vectors, strict=True):
                await cur.execute(
                    """
                    insert into public.document_chunks
                      (document_id, product_id, user_id, chunk_index, page_number, locator,
                       content, content_sha256, token_count, embedding, embedding_model,
                       embedding_dim, index_version)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::extensions.vector, %s, %s, %s)
                    on conflict (document_id, index_version, chunk_index) do update
                      set content        = excluded.content,
                          content_sha256 = excluded.content_sha256,
                          token_count    = excluded.token_count,
                          page_number    = excluded.page_number,
                          locator        = excluded.locator,
                          embedding      = excluded.embedding,
                          embedding_model = excluded.embedding_model,
                          embedding_dim  = excluded.embedding_dim
                    """,
                    (
                        document_id,
                        document["product_id"],
                        document["user_id"],
                        chunk.chunk_index,
                        chunk.page_number,
                        chunk.locator,
                        chunk.content,
                        chunk.content_sha256,
                        chunk.token_count,
                        vector,
                        embeddings.model,
                        embeddings.dimensions,
                        index_version,
                    ),
                )

        await execute(
            conn,
            """
            update public.product_documents
               set status            = 'ready',
                   extraction_method = %(method)s,
                   page_count        = %(page_count)s,
                   chunk_count       = %(chunk_count)s,
                   content_sha256    = %(identity)s,
                   index_version     = %(index_version)s,
                   embedding_model   = %(embedding_model)s,
                   embedding_dim     = %(embedding_dim)s,
                   error_code        = null,
                   error_message      = %(notes)s,
                   indexed_at        = now()
             where id = %(id)s
            """,
            {
                "id": document_id,
                "method": extraction_method,
                "page_count": page_count,
                "chunk_count": len(chunks),
                "identity": identity,
                "index_version": index_version,
                "embedding_model": embeddings.model,
                "embedding_dim": embeddings.dimensions,
                # `error_message` doubles as the place a *non-fatal* note lands
                # (a truncated scan, pages with no text), because the user needs
                # to know the index is incomplete even though it is usable.
                "notes": " ".join(notes) if notes else None,
            },
        )

    return len(chunks)


__all__ = ["DocumentGone", "IndexOutcome", "index_document", "index_version_for"]

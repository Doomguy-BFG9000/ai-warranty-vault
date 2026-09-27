"""Document endpoints.

The upload itself does not pass through this service. The client PUTs the file
straight into the private bucket with its own Supabase token — the same path a
receipt photo takes — and then registers it here. Two reasons: a 15 MB body never
has to traverse this process, and *Storage RLS* decides where a file may land,
rather than a permission check written in Python.

Registration is where the two halves are tied together. The path's user and
product segments must match the caller and the product, the product must be the
caller's (checked by an RLS-scoped query, so a stranger's id is indistinguishable
from a nonexistent one), and the object must be readable *by the caller's own
token*. Only then is a row written — which matters, because from that point the
indexing worker reads the file with the service-role key, and the row is what
authorises it.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, status

from app.auth import Caller, current_caller
from app.db import execute, fetch_all, fetch_one
from app.deps import Services, get_services
from app.errors import BadRequest, Conflict, NotFoundOrForbidden
from app.ingest.worker import enqueue_job
from app.schemas import ChunkResponse, DocumentResponse, RegisterDocumentRequest
from app.storage import delete_as_user, verify_readable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["documents"])

_DOCUMENT_COLUMNS = """
  d.id, d.product_id, d.title, d.kind::text as kind, d.status::text as status,
  d.mime_type, d.byte_size, d.page_count, d.chunk_count,
  d.extraction_method::text as extraction_method, d.index_version, d.embedding_model,
  d.error_code, d.error_message, d.indexed_at, d.created_at,
  j.state::text as job_state, j.attempts as job_attempts
"""

_DOCUMENT_FROM = """
from public.product_documents d
left join public.document_index_jobs j
       on j.document_id = d.id and j.state in ('queued', 'running')
"""


def _to_response(row: dict, *, active_index_version: str) -> DocumentResponse:
    return DocumentResponse(
        id=row["id"],
        product_id=row["product_id"],
        title=row["title"],
        kind=row["kind"],
        status=row["status"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        page_count=row["page_count"],
        chunk_count=row["chunk_count"] or 0,
        extraction_method=row["extraction_method"],
        index_version=row["index_version"],
        embedding_model=row["embedding_model"],
        error_code=row["error_code"],
        notice=row["error_message"],
        indexed_at=row["indexed_at"].isoformat() if row["indexed_at"] else None,
        created_at=row["created_at"].isoformat(),
        job_state=row["job_state"],
        job_attempts=row["job_attempts"],
        # Indexed under an older embedding space or chunker. Surfaced rather than
        # hidden: its chunks are excluded from search, so "ready" alone would be a
        # lie.
        stale_index=bool(
            row["status"] == "ready"
            and row["index_version"]
            and row["index_version"] != active_index_version
        ),
    )


@router.post(
    "/products/{product_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_document(
    product_id: uuid.UUID,
    payload: RegisterDocumentRequest,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> DocumentResponse:
    settings = services.settings

    if payload.byte_size > settings.max_document_bytes:
        raise BadRequest(
            f"That file is larger than the {settings.max_document_bytes // (1024 * 1024)} MB limit."
        )

    expected_prefix = f"{caller.user_id}/{product_id}/"
    if not payload.storage_path.startswith(expected_prefix):
        # Not a security control on its own — the CHECK constraint and the storage
        # policies are — but it turns a constraint violation into a clear 400.
        raise BadRequest("storage_path must be {user_id}/{product_id}/{filename}.")

    async with services.db.user_scope(caller.claims) as conn:
        product = await fetch_one(
            conn, "select id from public.products where id = %s", (product_id,)
        )
        if product is None:
            raise NotFoundOrForbidden("product")

    # The caller's own token must be able to read the object. This is the check
    # that makes the later service-role download safe.
    token_readable = await verify_readable(
        settings, payload.storage_path, _bearer(caller)
    )
    if not token_readable:
        raise BadRequest(
            "That upload could not be read. Upload the file to the product-documents "
            "bucket first, then register it."
        )

    async with services.db.user_scope(caller.claims) as conn:
        existing = await fetch_one(
            conn,
            "select id from public.product_documents where storage_path = %s",
            (payload.storage_path,),
        )
        if existing is not None:
            raise Conflict("That file is already attached to this product.")

        row = await fetch_one(
            conn,
            """
            insert into public.product_documents
              (product_id, user_id, title, kind, storage_path, mime_type, byte_size)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                product_id,
                caller.user_id,
                payload.title.strip(),
                payload.kind,
                payload.storage_path,
                payload.mime_type,
                payload.byte_size,
            ),
        )

    assert row is not None
    document_id = row["id"]
    await enqueue_job(services.db, document_id, caller.user_id, settings)

    return await get_document(document_id, caller=caller, services=services)


@router.get("/products/{product_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    product_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> list[DocumentResponse]:
    async with services.db.user_scope(caller.claims) as conn:
        rows = await fetch_all(
            conn,
            f"select {_DOCUMENT_COLUMNS} {_DOCUMENT_FROM} where d.product_id = %s order by d.created_at desc",
            (product_id,),
        )
    return [_to_response(row, active_index_version=services.index_version) for row in rows]


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> DocumentResponse:
    async with services.db.user_scope(caller.claims) as conn:
        row = await fetch_one(
            conn, f"select {_DOCUMENT_COLUMNS} {_DOCUMENT_FROM} where d.id = %s", (document_id,)
        )
    if row is None:
        raise NotFoundOrForbidden("document")
    return _to_response(row, active_index_version=services.index_version)


@router.post("/documents/{document_id}/reindex", response_model=DocumentResponse)
async def reindex_document(
    document_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> DocumentResponse:
    """Queue another indexing run.

    The job insert is guarded by a partial unique index on live jobs, so a
    double-tap cannot start two runs. Indexing itself is idempotent, so a retry
    after a *successful* run is harmless: it finds the same extracted text under
    the same index version and does nothing.
    """
    async with services.db.user_scope(caller.claims) as conn:
        row = await fetch_one(
            conn,
            "select id, user_id from public.product_documents where id = %s",
            (document_id,),
        )
    if row is None:
        raise NotFoundOrForbidden("document")

    queued = await enqueue_job(services.db, document_id, caller.user_id, services.settings)
    if not queued:
        raise Conflict("This document is already being indexed.")

    return await get_document(document_id, caller=caller, services=services)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> None:
    """Delete a document, its chunks, its embeddings and its stored file.

    The row delete cascades to chunks, embeddings and any queued job, so nothing
    searchable survives it. A worker that is mid-index on this document holds a row
    lock (`select ... for update`), so the two serialise: whichever commits first,
    the end state has no chunks and no row. See app/ingest/indexer.py.

    The stored object is removed afterwards with the caller's own token. A failure
    there is logged, not raised — the row is already gone, and a user should not be
    told the delete failed because a file lingered.
    """
    async with services.db.user_scope(caller.claims) as conn:
        row = await fetch_one(
            conn,
            "select id, storage_path from public.product_documents where id = %s",
            (document_id,),
        )
        if row is None:
            raise NotFoundOrForbidden("document")
        await execute(conn, "delete from public.product_documents where id = %s", (document_id,))

    await delete_as_user(services.settings, row["storage_path"], _bearer(caller))


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkResponse])
async def list_chunks(
    document_id: uuid.UUID,
    page: int | None = Query(default=None, ge=1, description="Only chunks from this page."),
    limit: int = Query(default=50, ge=1, le=200),
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> list[ChunkResponse]:
    """The chunks behind a citation — this is what "open the source" opens.

    Scoped to the active index version so a stale chunk set from a previous
    embedding model is never shown as the text an answer was based on.
    """
    async with services.db.user_scope(caller.claims) as conn:
        document = await fetch_one(
            conn, "select id from public.product_documents where id = %s", (document_id,)
        )
        if document is None:
            raise NotFoundOrForbidden("document")

        rows = await fetch_all(
            conn,
            """
            select id as chunk_id, document_id, chunk_index, page_number, locator,
                   content, token_count
            from public.document_chunks
            where document_id = %(document_id)s
              and index_version = %(index_version)s
              and (%(page)s::int is null or page_number = %(page)s::int)
            order by chunk_index
            limit %(limit)s
            """,
            {
                "document_id": document_id,
                "index_version": services.index_version,
                "page": page,
                "limit": limit,
            },
        )
    return [ChunkResponse(**row) for row in rows]


def _bearer(caller: Caller) -> str:
    """The caller's own token, used for Storage calls made on their behalf.

    Held on `Caller.access_token` rather than in `Caller.claims`, because the claims
    dict is written into `request.jwt.claims` inside Postgres and a bearer token
    does not belong there.
    """
    if not caller.access_token:
        raise BadRequest("Missing access token for storage access.")
    return caller.access_token

"""Request and response shapes.

Note what is absent from every request model: a user id. Identity comes from the
bearer token, so there is no field to forge. Product and document ids are accepted,
but every one of them is resolved through an RLS-scoped query before it is used,
which is why a stranger's id behaves exactly like a nonexistent one.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DocumentKind = Literal["manual", "warranty_card", "service_terms", "other"]
SearchMode = Literal["vector", "lexical", "hybrid"]

SUPPORTED_UPLOAD_TYPES = ("application/pdf", "text/plain", "image/jpeg", "image/png", "image/webp")


class RegisterDocumentRequest(BaseModel):
    """Registers an object the client has already uploaded to the private bucket.

    The upload itself goes straight to Supabase Storage with the user's own token,
    exactly like a receipt photo: it keeps large bodies out of this service, and it
    keeps Storage RLS as the thing that decides where a file may land.
    """

    storage_path: str = Field(
        max_length=512,
        description="Object path in the product-documents bucket: {user_id}/{product_id}/{name}",
    )
    title: str = Field(min_length=1, max_length=200)
    kind: DocumentKind = "other"
    mime_type: str
    byte_size: int = Field(gt=0)

    @field_validator("mime_type")
    @classmethod
    def _supported(cls, value: str) -> str:
        if value not in SUPPORTED_UPLOAD_TYPES:
            raise ValueError(f"unsupported mime_type; expected one of {', '.join(SUPPORTED_UPLOAD_TYPES)}")
        return value

    @field_validator("storage_path")
    @classmethod
    def _no_traversal(cls, value: str) -> str:
        # The database also has a CHECK constraint pinning the whole shape; this
        # catches it earlier and with a better message.
        if value.startswith("/") or ".." in value or "\\" in value:
            raise ValueError("storage_path must be a plain relative object path")
        return value


class DocumentResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    title: str
    kind: str
    status: str
    mime_type: str
    byte_size: int
    page_count: int | None = None
    chunk_count: int = 0
    extraction_method: str | None = None
    index_version: str | None = None
    embedding_model: str | None = None
    error_code: str | None = None
    # Carries both a failure reason and a non-fatal note ("only the first 12 pages
    # of this scan were read"), so the app can always tell the user what it knows.
    notice: str | None = None
    indexed_at: str | None = None
    created_at: str
    # Live indexing job, when there is one. The app uses this to distinguish
    # "failed" from "failed, retrying".
    job_state: str | None = None
    job_attempts: int | None = None
    stale_index: bool = False


class ChunkResponse(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    page_number: int | None
    locator: str
    content: str
    token_count: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    product_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=25)
    mode: SearchMode | None = None


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str | None
    product_id: uuid.UUID
    page_number: int | None
    locator: str
    snippet: str
    score: float
    vector_similarity: float | None = None
    lexical_rank: float | None = None
    vector_position: int | None = None
    lexical_position: int | None = None


class SearchResponse(BaseModel):
    mode: SearchMode
    top_k: int
    index_version: str
    hits: list[SearchHit]
    # True when the user has no indexed documents at all in this scope, so the app
    # can offer "add a document" instead of "no results for this query".
    no_documents: bool = False


class CreateThreadRequest(BaseModel):
    product_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ResumeRequest(BaseModel):
    """Answers whatever the graph is currently waiting for.

    A clarification resume carries `answer`. A draft decision carries `decision`
    plus, optionally, the text the user edited — which is what gets stored as the
    approved version.
    """

    answer: str | None = Field(default=None, max_length=4000)
    decision: Literal["approve", "reject"] | None = None
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=8000)


class Citation(BaseModel):
    label: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str | None
    page_number: int | None
    locator: str


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[Citation] = []
    outcome: str | None = None
    # True when the answer rests on the user's saved product record and the warranty
    # dates computed from it, rather than on a document passage. Reported separately
    # from citations so the app can label it as such — a computed date must never be
    # presented as though a document had stated it.
    used_product_facts: bool = False
    created_at: str


class PendingDraft(BaseModel):
    draft_id: uuid.UUID
    version: int
    status: str
    subject: str
    body: str
    citations: list[Citation] = []
    missing_information: list[str] = []
    approved_subject: str | None = None
    approved_body: str | None = None
    approved_at: str | None = None


class AwaitingInput(BaseModel):
    """What the conversation is waiting for, in product terms.

    Deliberately says nothing about graph internals: no node names, no checkpoint
    ids, no raw interrupt payload. Those are available to a developer on the debug
    router, under their own token.
    """

    kind: Literal["clarification", "claim_draft"]
    question: str | None = None
    draft: PendingDraft | None = None


class ThreadStateResponse(BaseModel):
    thread_id: uuid.UUID
    product_id: uuid.UUID | None
    title: str | None
    messages: list[MessageResponse] = []
    awaiting: AwaitingInput | None = None
    latest_draft: PendingDraft | None = None
    updated_at: str


class TurnResponse(BaseModel):
    thread_id: uuid.UUID
    outcome: str
    message: MessageResponse | None = None
    awaiting: AwaitingInput | None = None
    draft_result: dict[str, Any] | None = None

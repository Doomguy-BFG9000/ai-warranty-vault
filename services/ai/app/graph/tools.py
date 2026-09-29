"""The model's tools: two read-only lookups, owner fixed by the server.

Both tools are built by a factory that closes over the *verified* caller. There is
no `user_id` parameter for the model to fill in, and no parameter it could set to
widen the scope: the product argument is validated against the caller's own rows
through an RLS-scoped query, so a product id belonging to someone else behaves
exactly like one that does not exist.

What the model cannot reach, by construction rather than by instruction: arbitrary
SQL, the filesystem, outbound HTTP, any table other than through these two
queries, and any write at all. The tool surface *is* the sandbox.

Tool results also carry their chunks back into graph state via `Command`, so the
answering step scores against the exact rows the search returned rather than
re-parsing them out of a message. That is what makes "every citation resolves to a
retrieved, accessible chunk" checkable instead of hopeful.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.config import Settings
from app.db import Database, fetch_all, fetch_one
from app.providers import EmbeddingHandle, embed_query
from app.rag.retriever import RetrievedChunk, apply_relevance_floor, fit_context, search_chunks

logger = logging.getLogger(__name__)

# How much of a chunk the model sees in a tool result. The full text goes into the
# sources block later; this keeps a wide search from eating the context window
# before the answering step gets a turn.
_TOOL_PREVIEW_CHARS = 700


class SearchDocumentsInput(BaseModel):
    query: str = Field(
        description=(
            "What to look for, in the user's own words. Include error codes, model "
            "numbers and part numbers verbatim — they are matched literally as well "
            "as semantically."
        )
    )
    product_id: str | None = Field(
        default=None,
        description="Restrict to one product. Omit to search every document in this conversation's scope.",
    )


class ProductFactsInput(BaseModel):
    product_id: str | None = Field(
        default=None,
        description="Which product. Omit for the product this conversation is about.",
    )


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        # A malformed id is treated as "no such product", like any other id the
        # caller does not own. The model gets a plain not-found, not a stack trace.
        return None


def chunk_to_state(chunk: RetrievedChunk) -> dict[str, Any]:
    """Graph-state form of a chunk: plain JSON, so it survives checkpointing."""
    return {
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "product_id": str(chunk.product_id),
        "document_title": chunk.document_title,
        "page_number": chunk.page_number,
        "locator": chunk.locator,
        "content": chunk.content,
        "token_count": chunk.token_count,
        "score": chunk.score,
        "vector_position": chunk.vector_position,
        "lexical_position": chunk.lexical_position,
    }


def chunk_from_state(record: dict[str, Any]) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(record["chunk_id"]),
        document_id=uuid.UUID(record["document_id"]),
        product_id=uuid.UUID(record["product_id"]),
        page_number=record.get("page_number"),
        locator=record.get("locator") or "",
        content=record.get("content") or "",
        token_count=int(record.get("token_count") or 0),
        score=float(record.get("score") or 0.0),
        vector_similarity=None,
        lexical_rank=None,
        vector_position=record.get("vector_position"),
        lexical_position=record.get("lexical_position"),
        document_title=record.get("document_title"),
    )


async def load_product_facts(
    db: Database, claims: dict[str, Any], product_id: uuid.UUID
) -> dict[str, Any]:
    """The user's saved record for a product, plus the warranty dates computed in SQL.

    Factored out of the tool so the answering step can load it directly. A question
    about dates must not depend on the model having chosen to call a tool: the query
    is cheap, owner-scoped, and the difference between answering and refusing.

    Returns {} when the product is not the caller's — same as a product that does not
    exist, which is the point.
    """
    async with db.user_scope(claims) as conn:
        row = await fetch_one(
            conn,
            """
            select p.name, p.merchant, p.category, p.price_cents, p.currency,
                   p.purchase_date, p.warranty_months, p.warranty_expires_at,
                   f.days_remaining, f.status
            from public.products p
            -- Warranty arithmetic is done in SQL from stored fields. The model is
            -- told the answer; it is never asked to work out a date.
            join public.product_warranty_facts(p.id) f on true
            where p.id = %s
            """,
            (product_id,),
        )
        documents = await fetch_all(
            conn,
            """
            select title, kind::text as kind, status::text as status, page_count, chunk_count
            from public.product_documents
            where product_id = %s
            order by created_at
            """,
            (product_id,),
        )

    if row is None:
        return {}

    return {
        "found": True,
        "product_id": str(product_id),
        "name": row["name"],
        "merchant": row["merchant"],
        "category": row["category"],
        "price": (
            f"{row['price_cents'] / 100:.2f} {row['currency']}"
            if row["price_cents"] is not None
            else None
        ),
        "purchase_date": str(row["purchase_date"]) if row["purchase_date"] else None,
        "warranty_months": row["warranty_months"],
        "warranty_expires_at": (
            str(row["warranty_expires_at"]) if row["warranty_expires_at"] else None
        ),
        "days_remaining": row["days_remaining"],
        "warranty_window": row["status"],
        "documents": [
            {
                "title": doc["title"],
                "kind": doc["kind"],
                "indexed": doc["status"] == "ready",
                "pages": doc["page_count"],
                "passages": doc["chunk_count"],
            }
            for doc in documents
        ],
        "caveat": (
            "The warranty window is computed from the saved purchase date and "
            "warranty length. It does not establish that any particular fault "
            "is covered."
        ),
    }


def build_tools(
    *,
    db: Database,
    settings: Settings,
    embeddings: EmbeddingHandle,
    claims: dict[str, Any],
    index_version: str,
    thread_product_id: uuid.UUID | None,
) -> list[StructuredTool]:
    """Two tools, scoped to one verified caller and one conversation."""

    async def _resolve_product(requested: str | None) -> uuid.UUID | None:
        """Pick the product to act on, and confirm the caller owns it.

        A model-supplied id is only honoured after an RLS-scoped lookup finds it.
        When the conversation is already about a product, a *different* id is
        refused outright rather than silently redirected — a question asked about
        one product must not be answered from another one's paperwork.
        """
        candidate = _as_uuid(requested) or thread_product_id
        if candidate is None:
            return None
        if thread_product_id is not None and candidate != thread_product_id:
            return None
        async with db.user_scope(claims) as conn:
            row = await fetch_one(
                conn, "select id from public.products where id = %s", (candidate,)
            )
        return candidate if row is not None else None

    async def search_product_documents(
        query: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        product_id: str | None = None,
    ) -> Command:
        product = await _resolve_product(product_id)
        if product is None and thread_product_id is not None:
            payload = {
                "results": [],
                "note": "That product is not available in this conversation.",
            }
            return Command(
                update={
                    "messages": [
                        ToolMessage(json.dumps(payload), tool_call_id=tool_call_id, name="search_product_documents")
                    ]
                }
            )

        vector = await embed_query(embeddings, query)
        async with db.user_scope(claims) as conn:
            found = await search_chunks(
                conn,
                query_embedding=vector,
                query_text=query,
                product_id=product,
                top_k=settings.retrieval_top_k,
                mode=settings.retrieval_mode,
                candidate_count=settings.retrieval_candidates,
                index_version=index_version,
            )

        chunks = fit_context(
            apply_relevance_floor(found, settings, mode=settings.retrieval_mode),
            settings.context_token_budget,
        )

        records = [chunk_to_state(chunk) for chunk in chunks]
        payload = {
            "results": [
                {
                    "chunk_id": record["chunk_id"],
                    "document_title": record["document_title"],
                    "page_number": record["page_number"],
                    "locator": record["locator"],
                    "content": record["content"][:_TOOL_PREVIEW_CHARS],
                }
                for record in records
            ],
            "note": (
                "No matching text in this product's documents."
                if not records
                else f"{len(records)} passage(s) found."
            ),
        }

        return Command(
            update={
                # Accumulated in state, so the answering step scores against the
                # rows the database returned rather than the text in this message.
                "sources": records,
                "messages": [
                    ToolMessage(
                        json.dumps(payload, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                        name="search_product_documents",
                    )
                ],
            }
        )

    async def get_product_facts(
        tool_call_id: Annotated[str, InjectedToolCallId],
        product_id: str | None = None,
    ) -> Command:
        product = await _resolve_product(product_id)
        payload: dict[str, Any] = {}
        if product is not None:
            payload = await load_product_facts(db, claims, product)
        if not payload:
            payload = {"found": False, "note": "No such product for this user."}

        return Command(
            update={
                "facts": payload if payload.get("found") else {},
                "messages": [
                    ToolMessage(
                        json.dumps(payload, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                        name="get_product_facts",
                    )
                ],
            }
        )

    return [
        StructuredTool.from_function(
            coroutine=search_product_documents,
            name="search_product_documents",
            description=(
                "Search the documents attached to the user's product (manual, warranty card, "
                "service terms) and return the matching passages with their page numbers. "
                "Use this before answering anything about how the product works or what its "
                "warranty says."
            ),
            args_schema=SearchDocumentsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_product_facts,
            name="get_product_facts",
            description=(
                "Read the user's saved record for a product: name, retailer, purchase date, "
                "warranty length, the computed expiry date and which documents are indexed. "
                "Use this for dates and product identity, never to decide whether a fault is covered."
            ),
            args_schema=ProductFactsInput,
        ),
    ]

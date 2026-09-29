"""Developer surface.

Separate from the product API on purpose. The user-facing endpoints never expose a
node name, a checkpoint id, a raw interrupt payload or a similarity score — those
are implementation detail and leaking them into a UI is how a UI ends up depending
on them.

Everything here still runs under the caller's own token and their own RLS scope, so
"debug" does not mean "sees other people's documents". There is no admin mode.

What it is for, concretely: watching a run happen (`/threads/{id}/trace`), and
answering "why did retrieval pick that?" by running all three strategies side by
side on one query (`/retrieval/compare`).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth import Caller, current_caller
from app.db import fetch_all, fetch_one
from app.deps import Services, get_services
from app.errors import NotFoundOrForbidden
from app.providers import embed_query
from app.rag.retriever import search_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/debug", tags=["debug"])


@router.post("/retrieval/compare")
async def compare_retrieval(
    query: str = Query(min_length=1, max_length=1000),
    product_id: uuid.UUID | None = Query(default=None),
    top_k: int = Query(default=8, ge=1, le=25),
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    """Run the same query through vector, lexical and hybrid retrieval.

    The one tool that actually answers "should this be hybrid?" for a given corpus,
    because it shows the same question's three rankings with the scores that
    produced them.
    """
    embeddings = services.require_embeddings()
    vector = await embed_query(embeddings, query)

    out: dict[str, Any] = {"query": query, "index_version": services.index_version, "modes": {}}
    async with services.db.user_scope(caller.claims) as conn:
        for mode in ("vector", "lexical", "hybrid"):
            hits = await search_chunks(
                conn,
                query_embedding=vector,
                query_text=query,
                product_id=product_id,
                top_k=top_k,
                mode=mode,
                candidate_count=services.settings.retrieval_candidates,
                per_document_limit=services.settings.retrieval_per_document,
                index_version=services.index_version,
            )
            out["modes"][mode] = [
                {
                    "rank": position + 1,
                    "chunk_id": str(hit.chunk_id),
                    "document_title": hit.document_title,
                    "page_number": hit.page_number,
                    "locator": hit.locator,
                    "score": hit.score,
                    "vector_similarity": hit.vector_similarity,
                    "lexical_rank": hit.lexical_rank,
                    "vector_position": hit.vector_position,
                    "lexical_position": hit.lexical_position,
                    "preview": hit.content[:200],
                }
                for position, hit in enumerate(hits)
            ]
    return out


@router.get("/threads/{thread_id}/trace")
async def thread_trace(
    thread_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    """The graph's own view of a conversation: where it is and how it got there."""
    async with services.db.user_scope(caller.claims) as conn:
        thread = await fetch_one(
            conn,
            "select id, product_id from public.ai_threads where id = %s",
            (thread_id,),
        )
    if thread is None:
        raise NotFoundOrForbidden("conversation")

    from app.graph.build import build_graph

    graph = build_graph(
        db=services.db,
        settings=services.settings,
        embeddings=services.require_embeddings(),
        telemetry=services.telemetry,
        claims=caller.claims,
        thread_id=thread["id"],
        user_id=caller.user_id,
        product_id=thread["product_id"],
        checkpointer=services.checkpointer,
    )
    config = {"configurable": {"thread_id": str(thread_id)}}
    snapshot = await graph.aget_state(config)

    values = snapshot.values or {}
    messages = values.get("messages") or []

    history: list[dict[str, Any]] = []
    async for older in graph.aget_state_history(config, limit=20):
        history.append(
            {
                "checkpoint_id": (older.config or {}).get("configurable", {}).get("checkpoint_id"),
                "next": list(older.next or ()),
                "created_at": older.created_at,
                "writes": sorted((older.metadata or {}).get("writes", {}) or {}),
                "step": (older.metadata or {}).get("step"),
            }
        )

    return {
        "thread_id": str(thread_id),
        "next_nodes": list(snapshot.next or ()),
        "interrupts": [
            {"id": getattr(item, "id", None), "value": getattr(item, "value", None)}
            for item in (getattr(snapshot, "interrupts", None) or ())
        ],
        "intent": values.get("intent"),
        "route_reason": values.get("route_reason"),
        "tool_steps": values.get("tool_steps"),
        "outcome": values.get("outcome"),
        "messages": [
            {
                "type": getattr(message, "type", None),
                "tool_calls": [call.get("name") for call in getattr(message, "tool_calls", []) or []],
                "name": getattr(message, "name", None),
                "characters": len(str(getattr(message, "content", "") or "")),
            }
            for message in messages
        ],
        "sources": [
            {
                "chunk_id": source.get("chunk_id"),
                "document_title": source.get("document_title"),
                "page_number": source.get("page_number"),
                "score": source.get("score"),
                "vector_position": source.get("vector_position"),
                "lexical_position": source.get("lexical_position"),
            }
            for source in values.get("sources") or []
        ],
        "checkpoint_history": history,
    }


@router.get("/operations")
async def recent_operations(
    limit: int = Query(default=50, ge=1, le=200),
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    """Recent stage records for this caller.

    Read with the service scope because the log table denies `authenticated`
    outright, then filtered to the caller's own rows here. Content is not in this
    table at all, so there is nothing to redact — see app/telemetry.py.
    """
    async with services.db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            """
            select operation_id, stage, status, duration_ms, model, prompt_version,
                   input_tokens, output_tokens, detail, error_code, created_at
            from public.ai_operation_log
            where user_id = %s
            order by created_at desc
            limit %s
            """,
            (caller.user_id, limit),
        )
    return [
        {
            "operation_id": str(row["operation_id"]),
            "stage": row["stage"],
            "status": row["status"],
            "duration_ms": row["duration_ms"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "detail": row["detail"],
            "error_code": row["error_code"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]

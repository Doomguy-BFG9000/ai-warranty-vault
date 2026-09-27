"""Conversations: asking questions, answering clarifications, deciding on drafts.

Ownership is re-established on *every* call, including resumes. The graph's own
checkpoint tables have no RLS — they are service-only, and `authenticated` has no
privilege on that schema at all — so the thread is looked up in `public.ai_threads`
under the caller's RLS scope first, and only then is a graph bound to that thread
id. A resume with someone else's thread id is a 404 before LangGraph is involved.

The transcript lives in `public.ai_messages` rather than being reconstructed from
checkpoints. Checkpoints are the graph's working state — tool calls, scratch,
serialised message objects — and their shape belongs to LangGraph. What the app
renders is a product surface with its own contract (validated citations, an
explicit outcome), so it gets its own table, and reopening the app after a cold
start restores the conversation without deserialising graph internals.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from psycopg.types.json import Jsonb

from app.auth import Caller, current_caller
from app.db import execute, fetch_all, fetch_one
from app.deps import Services, get_services
from app.errors import BadRequest, NotFoundOrForbidden
from app.graph.build import build_graph
from app.rag.answer import NO_ANSWER_TEXT
from app.schemas import (
    AwaitingInput,
    CreateThreadRequest,
    MessageResponse,
    PendingDraft,
    ResumeRequest,
    SendMessageRequest,
    ThreadStateResponse,
    TurnResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])

# What the app shows when the graph is waiting for a draft decision.
_DRAFT_INTRO = (
    "Here is a draft service request based on your product record and its documents. "
    "Read it, edit anything you want changed, then approve or discard it. Nothing is sent."
)


# ── helpers ─────────────────────────────────────────────────────────────────
async def _load_thread(services: Services, caller: Caller, thread_id: uuid.UUID) -> dict[str, Any]:
    """Fetch the thread under RLS. This is the authorization check for every call."""
    async with services.db.user_scope(caller.claims) as conn:
        row = await fetch_one(
            conn,
            "select id, product_id, title, updated_at from public.ai_threads where id = %s",
            (thread_id,),
        )
    if row is None:
        raise NotFoundOrForbidden("conversation")
    return row


def _graph_for(services: Services, caller: Caller, thread: dict[str, Any]):
    if services.checkpointer is None:
        raise BadRequest("The conversation engine is not ready yet.")
    return build_graph(
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


def _config(thread_id: uuid.UUID) -> dict[str, Any]:
    # The graph's thread_id is the ai_threads row id, so one conversation has one
    # checkpoint lineage and there is no second mapping to keep in step.
    return {"configurable": {"thread_id": str(thread_id)}}


async def _record_message(
    services: Services,
    *,
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    outcome: str | None = None,
    meta: dict[str, Any] | None = None,
    caller: Caller | None = None,
) -> dict[str, Any]:
    """Append a turn.

    A user turn is written in the caller's own RLS scope. An assistant turn is
    written in the service scope, because a database trigger refuses an
    `authenticated` insert with role <> 'user' — a client must not be able to
    author an assistant message, since the citations on one are the server's
    assertion that the answer was grounded.
    """
    sql = """
        insert into public.ai_messages (thread_id, user_id, role, content, citations, outcome, meta)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning id, role::text as role, content, citations, outcome, created_at
    """
    params = (
        thread_id,
        user_id,
        role,
        content,
        Jsonb(citations or []),
        outcome,
        Jsonb(meta or {}),
    )

    if role == "user":
        assert caller is not None
        async with services.db.user_scope(caller.claims) as conn:
            row = await fetch_one(conn, sql, params)
    else:
        async with services.db.service_scope() as conn:
            row = await fetch_one(conn, sql, params)
            await execute(
                conn, "update public.ai_threads set updated_at = now() where id = %s", (thread_id,)
            )

    assert row is not None
    return row


def _message_response(row: dict[str, Any]) -> MessageResponse:
    return MessageResponse(
        id=row["id"],
        role=row["role"],
        content=row["content"],
        citations=row.get("citations") or [],
        outcome=row.get("outcome"),
        created_at=row["created_at"].isoformat(),
    )


def _draft_response(record: dict[str, Any] | None) -> PendingDraft | None:
    if not record:
        return None
    return PendingDraft(
        draft_id=uuid.UUID(str(record["draft_id"])),
        version=int(record["version"]),
        status=str(record["status"]),
        subject=str(record.get("subject") or ""),
        body=str(record.get("body") or ""),
        citations=record.get("citations") or [],
        missing_information=record.get("missing_information") or [],
        approved_subject=record.get("approved_subject"),
        approved_body=record.get("approved_body"),
        approved_at=record.get("approved_at"),
    )


async def _latest_draft(services: Services, caller: Caller, thread_id: uuid.UUID) -> dict[str, Any] | None:
    async with services.db.user_scope(caller.claims) as conn:
        row = await fetch_one(
            conn,
            """
            select id as draft_id, version, status::text as status, subject, body,
                   citations, approved_subject, approved_body, approved_at
            from public.claim_drafts
            where thread_id = %s
            order by version desc
            limit 1
            """,
            (thread_id,),
        )
    if row is None:
        return None
    record = dict(row)
    record["draft_id"] = str(record["draft_id"])
    if record.get("approved_at"):
        record["approved_at"] = record["approved_at"].isoformat()
    return record


def _awaiting_from(payload: dict[str, Any] | None, draft: dict[str, Any] | None) -> AwaitingInput | None:
    """Translate a raw interrupt payload into the product-level shape.

    The client never sees the payload itself, nor a node name, nor a checkpoint id.
    Those are on the debug router for developers, behind their own token.
    """
    if not payload:
        return None
    kind = payload.get("type")
    if kind == "clarification":
        return AwaitingInput(kind="clarification", question=str(payload.get("question") or ""))
    if kind == "claim_draft":
        merged = dict(draft or {})
        merged.update(
            {
                "draft_id": payload.get("draft_id") or merged.get("draft_id"),
                "version": payload.get("version") or merged.get("version") or 1,
                "status": merged.get("status") or "pending",
                "subject": payload.get("subject") or merged.get("subject") or "",
                "body": payload.get("body") or merged.get("body") or "",
                "citations": payload.get("citations") or merged.get("citations") or [],
                "missing_information": payload.get("missing_information") or [],
            }
        )
        return AwaitingInput(kind="claim_draft", draft=_draft_response(merged))
    return None


def _interrupt_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    return value if isinstance(value, dict) else None


# ── endpoints ───────────────────────────────────────────────────────────────
@router.post("/threads", response_model=ThreadStateResponse, status_code=status.HTTP_201_CREATED)
async def create_thread(
    payload: CreateThreadRequest,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> ThreadStateResponse:
    async with services.db.user_scope(caller.claims) as conn:
        if payload.product_id is not None:
            product = await fetch_one(
                conn, "select id from public.products where id = %s", (payload.product_id,)
            )
            if product is None:
                raise NotFoundOrForbidden("product")
        row = await fetch_one(
            conn,
            """
            insert into public.ai_threads (user_id, product_id, title)
            values (%s, %s, %s)
            returning id, product_id, title, updated_at
            """,
            (caller.user_id, payload.product_id, payload.title),
        )
    assert row is not None
    return ThreadStateResponse(
        thread_id=row["id"],
        product_id=row["product_id"],
        title=row["title"],
        messages=[],
        awaiting=None,
        latest_draft=None,
        updated_at=row["updated_at"].isoformat(),
    )


@router.get("/threads", response_model=list[ThreadStateResponse])
async def list_threads(
    product_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> list[ThreadStateResponse]:
    async with services.db.user_scope(caller.claims) as conn:
        rows = await fetch_all(
            conn,
            """
            select id, product_id, title, updated_at
            from public.ai_threads
            where (%(product_id)s::uuid is null or product_id = %(product_id)s::uuid)
            order by updated_at desc
            limit %(limit)s
            """,
            {"product_id": product_id, "limit": limit},
        )
    return [
        ThreadStateResponse(
            thread_id=row["id"],
            product_id=row["product_id"],
            title=row["title"],
            updated_at=row["updated_at"].isoformat(),
        )
        for row in rows
    ]


@router.get("/threads/{thread_id}", response_model=ThreadStateResponse)
async def get_thread(
    thread_id: uuid.UUID,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> ThreadStateResponse:
    """The conversation as the app should render it, restored from the backend.

    This is what makes "close the app mid-flow and come back" work: the transcript
    comes from `ai_messages`, and whatever the graph is still waiting for comes from
    its checkpoint, so a cold start resumes exactly where the user left off.
    """
    thread = await _load_thread(services, caller, thread_id)

    async with services.db.user_scope(caller.claims) as conn:
        rows = await fetch_all(
            conn,
            """
            select id, role::text as role, content, citations, outcome, created_at
            from public.ai_messages
            where thread_id = %s
            order by created_at
            """,
            (thread_id,),
        )

    draft = await _latest_draft(services, caller, thread_id)

    awaiting = None
    if services.checkpointer is not None and services.embeddings is not None:
        graph = _graph_for(services, caller, thread)
        snapshot = await graph.aget_state(_config(thread_id))
        pending = getattr(snapshot, "interrupts", None) or ()
        if pending:
            value = getattr(pending[0], "value", None)
            awaiting = _awaiting_from(value if isinstance(value, dict) else None, draft)

    return ThreadStateResponse(
        thread_id=thread_id,
        product_id=thread["product_id"],
        title=thread["title"],
        messages=[_message_response(row) for row in rows],
        awaiting=awaiting,
        latest_draft=_draft_response(draft),
        updated_at=thread["updated_at"].isoformat(),
    )


@router.post("/threads/{thread_id}/messages", response_model=TurnResponse)
async def send_message(
    thread_id: uuid.UUID,
    payload: SendMessageRequest,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> TurnResponse:
    thread = await _load_thread(services, caller, thread_id)
    graph = _graph_for(services, caller, thread)

    snapshot = await graph.aget_state(_config(thread_id))
    if getattr(snapshot, "interrupts", None):
        # A new question while the graph is mid-question would strand the pending
        # interrupt. Answer it (or discard the draft) first.
        raise BadRequest("This conversation is waiting for your answer. Reply to that first.")

    await _record_message(
        services,
        thread_id=thread_id,
        user_id=caller.user_id,
        role="user",
        content=payload.content,
        caller=caller,
    )

    state = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=payload.content)],
            "thread_id": str(thread_id),
            "user_id": str(caller.user_id),
            "product_id": str(thread["product_id"]) if thread["product_id"] else None,
            "tool_steps": 0,
            "clarification_asked": False,
        },
        _config(thread_id),
    )

    return await _turn_from_state(services, caller, thread_id, state)


@router.post("/threads/{thread_id}/resume", response_model=TurnResponse)
async def resume_thread(
    thread_id: uuid.UUID,
    payload: ResumeRequest,
    caller: Caller = Depends(current_caller),
    services: Services = Depends(get_services),
) -> TurnResponse:
    """Supply what the graph is waiting for.

    Works after a process restart, because the checkpoint is in Postgres rather
    than in this process's memory. Ownership is re-verified here, not assumed from
    whoever started the conversation.

    Resuming when nothing is pending is *not* an error: a retried request or a
    double-tapped Approve lands here, and the honest answer is the current state
    plus `applied: false`. Nothing is written twice — the approval is a conditional
    UPDATE behind a unique index, so at most one approved row can exist per thread.
    """
    thread = await _load_thread(services, caller, thread_id)
    graph = _graph_for(services, caller, thread)

    snapshot = await graph.aget_state(_config(thread_id))
    pending = getattr(snapshot, "interrupts", None) or ()
    if not pending:
        draft = await _latest_draft(services, caller, thread_id)
        return TurnResponse(
            thread_id=thread_id,
            outcome=(draft or {}).get("status") and f"draft_{draft['status']}" or "idle",
            awaiting=None,
            draft_result={
                "applied": False,
                "reason": f"already_{draft['status']}" if draft else "nothing_pending",
            },
        )

    interrupt_value = getattr(pending[0], "value", None) or {}
    kind = interrupt_value.get("type") if isinstance(interrupt_value, dict) else None

    if kind == "clarification":
        if not payload.answer:
            raise BadRequest("This conversation is waiting for an answer to its question.")
        await _record_message(
            services,
            thread_id=thread_id,
            user_id=caller.user_id,
            role="user",
            content=payload.answer,
            caller=caller,
        )
        resume_value: Any = {"answer": payload.answer}
    elif kind == "claim_draft":
        if payload.decision is None:
            raise BadRequest("Approve or discard the draft.")
        resume_value = {
            "decision": payload.decision,
            "subject": payload.subject,
            "body": payload.body,
        }
    else:
        raise BadRequest("This conversation is not waiting for input of that kind.")

    state = await graph.ainvoke(Command(resume=resume_value), _config(thread_id))
    return await _turn_from_state(services, caller, thread_id, state)


async def _turn_from_state(
    services: Services, caller: Caller, thread_id: uuid.UUID, state: dict[str, Any]
) -> TurnResponse:
    """Persist whatever the run produced and describe it in product terms."""
    draft = await _latest_draft(services, caller, thread_id)
    interrupt_payload = _interrupt_payload(state)

    meta = {
        "model": services.settings.answer_model,
        "index_version": services.index_version,
        "provider_mode": services.settings.provider_mode,
        "tool_steps": state.get("tool_steps", 0),
        "sources_considered": len(state.get("sources") or []),
    }

    if interrupt_payload and interrupt_payload.get("type") == "clarification":
        row = await _record_message(
            services,
            thread_id=thread_id,
            user_id=caller.user_id,
            role="assistant",
            content=str(interrupt_payload.get("question") or "Could you tell me a little more?"),
            outcome="clarification_needed",
            meta=meta,
        )
        return TurnResponse(
            thread_id=thread_id,
            outcome="clarification_needed",
            message=_message_response(row),
            awaiting=_awaiting_from(interrupt_payload, draft),
        )

    if interrupt_payload and interrupt_payload.get("type") == "claim_draft":
        row = await _record_message(
            services,
            thread_id=thread_id,
            user_id=caller.user_id,
            role="assistant",
            content=_DRAFT_INTRO,
            citations=interrupt_payload.get("citations") or [],
            outcome="draft_ready",
            meta=meta,
        )
        return TurnResponse(
            thread_id=thread_id,
            outcome="draft_ready",
            message=_message_response(row),
            awaiting=_awaiting_from(interrupt_payload, draft),
        )

    outcome = str(state.get("outcome") or "answered")
    draft_result = state.get("draft_result")

    if draft_result is not None:
        applied = bool(draft_result.get("applied"))
        status_word = (draft or {}).get("status") or "pending"
        content = (
            "Saved the version you approved. Nothing has been sent — it is yours to send."
            if status_word == "approved"
            else "Discarded that draft."
            if status_word == "rejected"
            else "The draft is unchanged."
        )
        row = await _record_message(
            services,
            thread_id=thread_id,
            user_id=caller.user_id,
            role="assistant",
            content=content,
            outcome=outcome,
            meta={**meta, "applied": applied},
        )
        return TurnResponse(
            thread_id=thread_id,
            outcome=outcome,
            message=_message_response(row),
            draft_result=draft_result,
        )

    answer = str(state.get("answer") or NO_ANSWER_TEXT)
    citations = state.get("citations") or []
    row = await _record_message(
        services,
        thread_id=thread_id,
        user_id=caller.user_id,
        role="assistant",
        content=answer,
        citations=citations,
        outcome=outcome,
        meta=meta,
    )
    return TurnResponse(
        thread_id=thread_id, outcome=outcome, message=_message_response(row), awaiting=None
    )

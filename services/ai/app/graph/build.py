"""The assistant graph.

```
                        ┌──────────┐
      user turn ───────►│  route   │  (LLM, structured: intent + is a question needed?)
                        └────┬─────┘
                  needs_clarification│          │ intent
                        ┌────▼─────┐ │          │
                        │ clarify  │ │          │
                        │ interrupt│ │          │
                        └────┬─────┘ │          │
                             └───────┤          │
                                     │          │
                                ┌────▼──────────▼────┐
                        ┌──────►│       agent        │  (LLM + 2 read-only tools)
                        │       └──┬────────┬────────┘
                        │   tool   │        │ done
                        │   calls  │        ├────────────── intent = question ──────┐
                   ┌────┴─────┐    │        │                                       │
                   │  tools   │◄───┘        └── intent = claim_draft ──┐            │
                   │ ToolNode │                                        │            │
                   └──────────┘                                  ┌─────▼──────┐  ┌──▼──────┐
                                                                 │ draft_claim│  │ compose │
                                                                 │  (LLM +    │  │  (LLM,  │
                                                                 │  DB write) │  │ grounded│
                                                                 └─────┬──────┘  │ answer) │
                                                                       │         └──┬──────┘
                                                                 ┌─────▼──────┐    │
                                                                 │await_       │    │
                                                                 │approval     │    │
                                                                 │ interrupt   │    │
                                                                 └─────┬──────┘    │
                                                                       │           │
                                                                 ┌─────▼──────┐    │
                                                                 │  finalize  │    │
                                                                 └─────┬──────┘    │
                                                                       └────► END ◄┘
```

Two things about this shape are load-bearing rather than decorative.

**The tool loop is bounded by the graph, not by the prompt.** `agent` counts its
own tool rounds and the conditional edge stops routing to `tools` past
`MAX_TOOL_STEPS`. A model that keeps asking for one more search gets an answer
composed from what it already has.

**Work and waiting are in different nodes.** Resuming a graph re-executes the
interrupted task from the beginning of its node, so anything expensive or
side-effecting placed next to an `interrupt()` would run again on every resume.
`draft_claim` does the model call and the database write and then finishes;
`await_approval` contains nothing but the `interrupt`. Re-running it costs nothing,
and the draft it is waiting on is found by idempotency key rather than recreated.
Wrapping a single model call in a graph would not have needed any of this — which
is rather the point of separating them.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from app.config import Settings
from app.db import Database
from app.graph.drafts import approve_draft, propose_draft, reject_draft
from app.graph.state import AssistantState
from app.graph.tools import build_tools, chunk_from_state
from app.providers import EmbeddingHandle, build_chat_model
from app.rag.answer import compose_answer, render_facts, render_sources, resolve_citations
from app.rag.prompts import (
    AGENT_PROMPT_VERSION,
    AGENT_SYSTEM,
    DRAFT_PROMPT_VERSION,
    DRAFT_SCHEMA,
    DRAFT_SYSTEM,
    ROUTER_PROMPT_VERSION,
    ROUTER_SCHEMA,
    ROUTER_SYSTEM,
)
from app.telemetry import Telemetry

logger = logging.getLogger(__name__)


def _last_user_text(state: AssistantState) -> str:
    for message in reversed(state.get("messages") or []):
        if getattr(message, "type", None) == "human":
            text = getattr(message, "text", None)
            return text if isinstance(text, str) else str(message.content)
    return ""


def build_graph(
    *,
    db: Database,
    settings: Settings,
    embeddings: EmbeddingHandle,
    telemetry: Telemetry,
    claims: dict[str, Any],
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    product_id: uuid.UUID | None,
    checkpointer,
):
    """Compile a graph bound to one verified caller and one conversation.

    The binding is the security model: the tools, the retrieval scope and every
    database write are fixed by `claims` at compile time. Nothing the model emits
    can change whose data the run touches.
    """
    tools = build_tools(
        db=db,
        settings=settings,
        embeddings=embeddings,
        claims=claims,
        index_version=_index_version(settings),
        thread_product_id=product_id,
    )
    chat = build_chat_model(settings)
    agent_model = chat.bind_tools(tools)

    # ── route ───────────────────────────────────────────────────────────────
    async def route(state: AssistantState) -> dict[str, Any]:
        question = _last_user_text(state)
        structured = chat.with_structured_output(ROUTER_SCHEMA)

        context = [
            {"role": "system", "content": ROUTER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Conversation is about product: {product_id or 'not specified'}\n"
                    f"Latest message: {question}"
                ),
            },
        ]
        async with telemetry.stage(
            "graph.route", user_id=user_id, model=settings.answer_model, prompt_version=ROUTER_PROMPT_VERSION
        ):
            decision = await structured.ainvoke(context)

        decision = decision if isinstance(decision, dict) else {}
        intent = decision.get("intent") or "question"
        needs = bool(decision.get("needs_clarification"))
        asked_before = bool(state.get("clarification_asked"))

        return {
            "intent": intent,
            "route_reason": str(decision.get("reason") or "")[:200],
            # One clarifying question per conversation turn cycle. Without this the
            # graph could ask, be answered, and decide it needs to ask again.
            "clarification_asked": asked_before,
            "_pending_question": str(decision.get("clarifying_question") or "") if needs and not asked_before else "",
        }

    def after_route(state: AssistantState) -> str:
        return "clarify" if state.get("_pending_question") else "agent"

    # ── clarify (interrupt) ─────────────────────────────────────────────────
    async def clarify(state: AssistantState) -> dict[str, Any]:
        """Ask one question and suspend. Nothing else happens in this node.

        The interrupt payload is what the app renders. On resume the graph
        re-enters this node, calls `interrupt` again, and LangGraph returns the
        value supplied with `Command(resume=...)` — which is why there must be no
        work here to repeat.
        """
        question = state.get("_pending_question") or "Could you give me a little more detail?"
        reply = interrupt({"type": "clarification", "question": question})
        answer = (reply or {}).get("answer") if isinstance(reply, dict) else reply
        return {
            "messages": [HumanMessage(content=str(answer or "").strip() or "(no answer given)")],
            "clarification_asked": True,
            "_pending_question": "",
        }

    # ── agent ───────────────────────────────────────────────────────────────
    async def agent(state: AssistantState) -> dict[str, Any]:
        history = [SystemMessage(content=AGENT_SYSTEM), *(state.get("messages") or [])]
        async with telemetry.stage(
            "graph.agent",
            user_id=user_id,
            model=settings.answer_model,
            prompt_version=AGENT_PROMPT_VERSION,
            step=state.get("tool_steps", 0),
        ):
            reply: AIMessage = await agent_model.ainvoke(history)

        steps = state.get("tool_steps", 0)
        if getattr(reply, "tool_calls", None):
            steps += 1
        return {"messages": [reply], "tool_steps": steps}

    def after_agent(state: AssistantState) -> str:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        wants_tools = bool(getattr(last, "tool_calls", None))
        if wants_tools and state.get("tool_steps", 0) <= settings.max_tool_steps:
            return "tools"
        if wants_tools:
            # Over budget. Not an error: the graph proceeds with what was gathered,
            # and the answering step is free to conclude that it is not enough.
            logger.info("tool budget exhausted for thread %s; composing from what we have", thread_id)
        return "draft_claim" if state.get("intent") == "claim_draft" else "compose"

    # ── compose ─────────────────────────────────────────────────────────────
    async def compose(state: AssistantState) -> dict[str, Any]:
        chunks = [chunk_from_state(record) for record in state.get("sources") or []]
        question = _last_user_text(state)

        async with telemetry.stage(
            "graph.compose",
            user_id=user_id,
            model=settings.answer_model,
            sources=len(chunks),
        ) as stage:
            result = await compose_answer(
                chat_model=chat, question=question, chunks=chunks, facts=state.get("facts") or None
            )
            stage.prompt_version = result.prompt_version
            stage.input_tokens = result.input_tokens
            stage.output_tokens = result.output_tokens
            stage.detail["outcome"] = result.outcome
            stage.detail["citations"] = len(result.citations)
            stage.detail["dropped_labels"] = len(result.dropped_labels)

        return {
            "messages": [AIMessage(content=result.answer)],
            "outcome": result.outcome,
            "answer": result.answer,
            "citations": result.citations,
        }

    # ── draft_claim ─────────────────────────────────────────────────────────
    async def draft_claim(state: AssistantState) -> dict[str, Any]:
        """Generate the draft and record it. Finishes before any waiting begins."""
        if product_id is None:
            return {
                "outcome": "clarification_needed",
                "answer": "Open this conversation from a product to draft a service request for it.",
                "citations": [],
            }

        chunks = [chunk_from_state(record) for record in state.get("sources") or []]
        facts = state.get("facts") or {}
        described = _last_user_text(state)

        content = [
            render_facts(facts),
            f"Sources:\n{render_sources(chunks)}" if chunks else "Sources: (none retrieved)",
            f"What the user reported, in their words:\n{described}",
        ]
        messages = [
            {"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": "\n\n".join(block for block in content if block)},
        ]

        async with telemetry.stage(
            "graph.draft",
            user_id=user_id,
            model=settings.answer_model,
            prompt_version=DRAFT_PROMPT_VERSION,
            sources=len(chunks),
        ) as stage:
            proposal = await chat.with_structured_output(DRAFT_SCHEMA).ainvoke(messages)
            proposal = proposal if isinstance(proposal, dict) else {}
            stage.detail["missing_information"] = len(proposal.get("missing_information") or [])

        subject = str(proposal.get("subject") or "Service request").strip()
        body = str(proposal.get("body") or "").strip()
        if not body:
            return {
                "outcome": "error",
                "answer": "I could not put a draft together. Please try again.",
                "citations": [],
            }

        citations, _dropped = resolve_citations(proposal.get("cited_sources") or [], chunks)

        record = await propose_draft(
            db,
            thread_id=thread_id,
            user_id=user_id,
            product_id=product_id,
            subject=subject,
            body=body,
            facts=facts,
            citations=citations,
        )
        record["missing_information"] = [
            str(item)[:200] for item in (proposal.get("missing_information") or [])
        ][:8]
        return {"draft": record, "outcome": "draft_ready", "citations": citations}

    def after_draft(state: AssistantState) -> str:
        return "await_approval" if state.get("draft") else END

    # ── await_approval (interrupt only) ─────────────────────────────────────
    async def await_approval(state: AssistantState) -> dict[str, Any]:
        draft = state.get("draft") or {}
        decision = interrupt(
            {
                "type": "claim_draft",
                "draft_id": draft.get("draft_id"),
                "version": draft.get("version"),
                "subject": draft.get("subject"),
                "body": draft.get("body"),
                "citations": draft.get("citations") or [],
                "missing_information": draft.get("missing_information") or [],
                "note": "Nothing is sent. Approving stores the text you approved.",
            }
        )
        return {"draft_decision": decision if isinstance(decision, dict) else {"decision": str(decision)}}

    # ── finalize ────────────────────────────────────────────────────────────
    async def finalize(state: AssistantState) -> dict[str, Any]:
        """Apply the human's decision. Safe to reach twice; writes at most once."""
        draft = state.get("draft") or {}
        decision = state.get("draft_decision") or {}
        draft_id = draft.get("draft_id")
        if not draft_id:
            return {"draft_result": {"applied": False, "reason": "no_draft"}}

        choice = str(decision.get("decision") or "").lower()
        if choice == "approve":
            # The user may have edited the text. What they approved is stored as
            # what they approved; the model's proposal stays untouched alongside it.
            result = await approve_draft(
                db,
                claims,
                draft_id=uuid.UUID(str(draft_id)),
                approved_subject=str(decision.get("subject") or draft.get("subject") or ""),
                approved_body=str(decision.get("body") or draft.get("body") or ""),
            )
            outcome = "draft_approved" if result.get("status") == "approved" else "draft_ready"
        elif choice == "reject":
            result = await reject_draft(db, claims, draft_id=uuid.UUID(str(draft_id)))
            outcome = "draft_rejected"
        else:
            result = {"applied": False, "reason": "no_decision"}
            outcome = "draft_ready"

        return {"draft_result": result, "outcome": outcome}

    graph = StateGraph(AssistantState)
    graph.add_node("route", route)
    graph.add_node("clarify", clarify)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("compose", compose)
    graph.add_node("draft_claim", draft_claim)
    graph.add_node("await_approval", await_approval)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", after_route, {"clarify": "clarify", "agent": "agent"})
    graph.add_edge("clarify", "agent")
    graph.add_conditional_edges(
        "agent",
        after_agent,
        {"tools": "tools", "compose": "compose", "draft_claim": "draft_claim"},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("compose", END)
    graph.add_conditional_edges("draft_claim", after_draft, {"await_approval": "await_approval", END: END})
    graph.add_edge("await_approval", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


def _index_version(settings: Settings) -> str:
    from app.ingest.indexer import index_version_for

    return index_version_for(settings)


__all__ = ["build_graph", "Command"]

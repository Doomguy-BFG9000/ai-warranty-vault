"""Graph state.

Kept flat and JSON-shaped on purpose: every field is checkpointed, and a state that
serialises cleanly is a state that survives a process restart without a custom
serializer. Chunks travel as dicts for the same reason.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


def merge_sources(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Accumulate retrieved chunks across searches, de-duplicated, order preserved.

    Order matters: source labels (S1, S2, …) are assigned by position, so a stable
    order means a label means the same thing for the whole turn. Duplicates are
    dropped because two searches often return the same clause, and citing "S2 and
    S5" when both are the same passage reads as two independent sources.
    """
    merged: list[dict[str, Any]] = list(existing or [])
    seen = {record.get("chunk_id") for record in merged}
    for record in new or []:
        chunk_id = record.get("chunk_id")
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        merged.append(record)
    return merged


class AssistantState(TypedDict, total=False):
    # Conversation
    messages: Annotated[list[Any], add_messages]

    # Identity. Set by the server when the run starts, never by the model, and
    # re-verified against public.ai_threads before every run and every resume.
    thread_id: str
    user_id: str
    product_id: str | None

    # Routing
    intent: str
    route_reason: str
    clarification_asked: bool
    # Set by `route`, consumed by `clarify`. Declared here because every state key
    # a node writes has to be a declared channel.
    _pending_question: str

    # Retrieval and facts
    sources: Annotated[list[dict[str, Any]], merge_sources]
    facts: dict[str, Any]
    tool_steps: int

    # Question outcome
    outcome: str
    answer: str
    citations: list[dict[str, Any]]
    # True when the answer rests on the saved product record rather than on a
    # document. Kept separate from citations so a computed date is never rendered
    # as though a document had stated it.
    used_product_facts: bool

    # Claim draft
    draft: dict[str, Any]
    draft_decision: dict[str, Any]
    draft_result: dict[str, Any]

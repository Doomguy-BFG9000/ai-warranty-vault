"""Composing an answer, and refusing to pretend one exists.

Source labels, not ids. The model is shown `S1 … Sn` and asked to cite those
labels. It never sees or produces a chunk id, a page number or a document name, so
there is nothing for it to get subtly wrong or invent: `resolve_citations` maps a
label back to the chunk *the server put under it*, and a label that was not in this
turn's context is dropped. Page numbers and document titles in a citation always
come out of the database row.

Abstention is a first-class outcome, not a phrasing. If the model claims to have
answered but cited nothing valid, the answer is replaced with a no-answer — an
uncited claim about someone's warranty is exactly the output this system exists to
avoid producing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.prompts import ANSWER_PROMPT_VERSION, ANSWER_SCHEMA, ANSWER_SYSTEM
from app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# Deliberately strict: "S3" and nothing else. "page 3", "S3 (warranty card)" and
# "[S3]" all normalise to the label; anything that is not a label in this turn's
# context is dropped rather than guessed at.
_LABEL_RE = re.compile(r"\bS(\d{1,3})\b", re.IGNORECASE)

NO_ANSWER_TEXT = (
    "I could not find this in the documents attached to this product. "
    "Adding the manual or the warranty card would let me answer it."
)


@dataclass
class AnswerResult:
    outcome: str  # 'answered' | 'no_answer' | 'conflicting_sources'
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    dropped_labels: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_version: str = ANSWER_PROMPT_VERSION


def label_for(position: int) -> str:
    return f"S{position + 1}"


def render_sources(chunks: list[RetrievedChunk]) -> str:
    """The sources block. One labelled, complete chunk per entry."""
    if not chunks:
        return "(no sources were retrieved)"
    parts: list[str] = []
    for position, chunk in enumerate(chunks):
        header = f"[{label_for(position)}] {chunk.document_title or 'document'}"
        if chunk.page_number is not None:
            header += f", page {chunk.page_number}"
        else:
            header += f", {chunk.locator}"
        parts.append(f"{header}\n{chunk.content}")
    return "\n\n".join(parts)


def render_facts(facts: dict[str, Any] | None) -> str:
    """Stored product data and computed dates, labelled as computed.

    Kept out of the sources block on purpose: these are not quotable document text
    and must not be citable as if they were. The caveat is spelled out because an
    open warranty window is routinely mistaken for "this fault is covered".
    """
    if not facts:
        return ""
    lines = ["Product facts (from the user's own saved record, computed by this system — not from a document):"]
    for key, value in facts.items():
        if value is None or key.startswith("_"):
            continue
        lines.append(f"- {key}: {value}")
    lines.append(
        "An open warranty window means the period has not lapsed. It is not evidence "
        "that any particular fault is covered."
    )
    return "\n".join(lines)


def resolve_citations(
    cited: list[str], chunks: list[RetrievedChunk]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map model-returned labels onto this turn's chunks.

    Returns (citations, dropped). A label is kept only if it resolves to a chunk
    that was actually in the context handed to the model on this turn — which, since
    the context came from an RLS-scoped query, is also a guarantee that every cited
    chunk belongs to the caller.
    """
    resolved: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for raw in cited:
        if not isinstance(raw, str):
            dropped.append(str(raw))
            continue
        match = _LABEL_RE.search(raw)
        if match is None:
            dropped.append(raw)
            continue
        index = int(match.group(1)) - 1
        if not 0 <= index < len(chunks):
            # Either a label from an earlier turn or one the model made up. Both
            # are "not part of this context", and both are dropped.
            dropped.append(raw)
            continue
        label = label_for(index)
        if label in seen:
            continue
        seen.add(label)
        resolved.append(chunks[index].as_source(label))

    return resolved, dropped


def enforce_grounding(
    outcome: str, answer: str, citations: list[dict[str, Any]], chunks: list[RetrievedChunk]
) -> tuple[str, str, list[dict[str, Any]]]:
    """The last gate before an answer is shown or stored."""
    if not chunks:
        # Nothing was retrieved, so there is nothing an answer could rest on,
        # whatever the model said.
        return "no_answer", NO_ANSWER_TEXT, []

    if outcome in ("answered", "conflicting_sources") and not citations:
        logger.info("answer claimed outcome=%s with no valid citation; downgraded", outcome)
        return "no_answer", NO_ANSWER_TEXT, []

    if outcome == "no_answer":
        # Trust an abstention. A no-answer with citations attached is confusing, so
        # they are dropped rather than rendered as support for a non-answer.
        return "no_answer", answer.strip() or NO_ANSWER_TEXT, []

    return outcome, answer.strip(), citations


async def compose_answer(
    *,
    chat_model,
    question: str,
    chunks: list[RetrievedChunk],
    facts: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> AnswerResult:
    """Ask the model for a grounded answer, then verify it is one."""
    if not chunks:
        # Skip the model call entirely: there is no context, so the only correct
        # output is the abstention, and paying for a round-trip to be told that is
        # waste.
        return AnswerResult(outcome="no_answer", answer=NO_ANSWER_TEXT)

    blocks = [render_facts(facts), f"Sources:\n{render_sources(chunks)}", f"Question: {question}"]
    user_content = "\n\n".join(block for block in blocks if block)

    messages: list[dict[str, Any]] = [{"role": "system", "content": ANSWER_SYSTEM}]
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_content})

    structured = chat_model.with_structured_output(ANSWER_SCHEMA, include_raw=True)
    raw = await structured.ainvoke(messages)

    if isinstance(raw, dict) and "parsed" in raw:
        parsed = raw.get("parsed") or {}
        usage_source = raw.get("raw")
    else:
        parsed = raw if isinstance(raw, dict) else {}
        usage_source = None

    outcome = str(parsed.get("outcome") or "no_answer")
    answer = str(parsed.get("answer") or "")
    cited = parsed.get("cited_sources") or []
    if not isinstance(cited, list):
        cited = []

    citations, dropped = resolve_citations(cited, chunks)
    outcome, answer, citations = enforce_grounding(outcome, answer, citations, chunks)

    input_tokens = output_tokens = None
    if usage_source is not None:
        from app.providers import usage_from_response

        input_tokens, output_tokens = usage_from_response(usage_source)

    if dropped:
        logger.info("dropped %s citation label(s) that were not in this turn's context", len(dropped))

    return AnswerResult(
        outcome=outcome,
        answer=answer,
        citations=citations,
        dropped_labels=dropped,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

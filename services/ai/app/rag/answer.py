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

**Two grounding channels, not one.** Retrieved passages are one. The user's saved
product record and the warranty dates computed from it in SQL are the other, and they
are just as grounded — more so, being arithmetic rather than retrieval. Treating
"no passages retrieved" as "nothing to answer from" made the assistant refuse
"how long is left on my warranty", a question it had the exact answer to and no
document could ever answer. So grounding is satisfied by a valid citation *or* by a
value from the record surviving into the text, and the two are reported separately, so a
date never looks like a document quotation.

**The outcome is the server's, not the model's.** `enforce_grounding` derives it from
what can be verified — a validated cross-document conflict, a citation resolving into
this turn's context, a fact value present in the text — and treats the model's
`outcome` field as one more claim to check. The live model returned `no_answer` while
its prose stated the exact expiry date it had been handed; believing the field there
produced an abstention heading above a real answer, and, in the other direction, would
let unsupported prose through simply for being labelled a refusal. Either way the
prose is never kept without grounding under it.

**A conflict is carried by structure, not by prose.** Prose is precisely where a model
picks a side while appearing not to. For `conflicting_sources` the model supplies one
neutral sentence plus one entry per position with its source label; the server renders
the sides itself and refuses the outcome unless two positions resolve to two different
documents.
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

# Lead-in for the server-rendered conflict block. Deliberately not a sentence the
# model wrote: it must not vary, and it must not lean.
CONFLICT_HEADING = {
    "en": "The documents do not agree:",
    "pl": "Dokumenty nie są zgodne:",
}


@dataclass
class AnswerResult:
    outcome: str  # 'answered' | 'no_answer' | 'conflicting_sources'
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    dropped_labels: list[str] = field(default_factory=list)
    # True when the answer rests on the user's saved record and the dates computed
    # from it. Surfaced to the app, which labels it as such — a computed date must
    # never be presented as if a document had said it.
    used_product_facts: bool = False
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


def resolve_conflict_positions(
    positions: Any, chunks: list[RetrievedChunk]
) -> list[dict[str, Any]]:
    """Map each claimed position onto the chunk its label names.

    Same rule as citations: a label that was not in this turn's context is dropped,
    so a "conflict" cannot be asserted against a source the server never supplied.
    """
    resolved: list[dict[str, Any]] = []
    if not isinstance(positions, list):
        return resolved

    for entry in positions:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("position") or "").strip()
        match = _LABEL_RE.search(str(entry.get("source") or ""))
        if not text or match is None:
            continue
        index = int(match.group(1)) - 1
        if not 0 <= index < len(chunks):
            continue
        chunk = chunks[index]
        resolved.append(
            {
                "position": text,
                **chunk.as_source(label_for(index)),
            }
        )
    return resolved


def render_conflict(answer: str, positions: list[dict[str, Any]]) -> str:
    """Build the conflict text from structure, so the sides cannot be buried.

    The model contributes one neutral sentence and the wording of each position; the
    ordering, the bullets and the attribution are the server's. Nothing here ranks the
    positions or adds a recommendation.
    """
    heading = CONFLICT_HEADING["pl"] if _looks_polish(answer) else CONFLICT_HEADING["en"]
    lines = [answer.strip(), "", heading]
    for entry in positions:
        where = entry.get("document_title") or "document"
        locator = entry.get("locator")
        lines.append(f"• {entry['position']} — {where}" + (f", {locator}" if locator else ""))
    return "\n".join(line for line in lines if line is not None).strip()


def _looks_polish(text: str) -> bool:
    """Crude, and only ever picks which fixed heading to use.

    Getting it wrong costs one line of English above Polish bullets, which is why a
    character test is proportionate here and a language-detection dependency is not.
    """
    return any(character in text for character in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")


# Only tokens carrying a digit count: a year, a count of days, a serial. Words are
# deliberately excluded — the brand and the product name are in the question too, so
# matching them would let any prose at all pass as fact-grounded. Tokens shorter than
# three characters are dropped for the same reason: "14" and "03" out of a date occur
# everywhere.
_FACT_TOKEN_RE = re.compile(r"[^\W_]*\d[^\W_]*", re.UNICODE)
_MIN_FACT_TOKEN = 3


def fact_tokens(text: str) -> set[str]:
    """Digit-bearing tokens in a piece of text, long enough to be distinctive."""
    found = (match.group(0).casefold() for match in _FACT_TOKEN_RE.finditer(text))
    return {token for token in found if len(token) >= _MIN_FACT_TOKEN}


def _fact_value_tokens(facts: dict[str, Any] | None) -> set[str]:
    if not facts:
        return set()
    tokens: set[str] = set()
    for key, value in facts.items():
        if value is None or key.startswith("_"):
            continue
        tokens |= fact_tokens(str(value))
    return tokens


def facts_support(answer: str, facts: dict[str, Any] | None) -> bool:
    """Whether the answer text actually carries a value from the product record.

    This is the server's own check on the second grounding channel, and it exists
    because the model's self-report is not reliable: the live model returned
    `no_answer` while stating the exact expiry date it had been handed. A date is
    the one thing in that answer the model could not have invented, so the presence
    of a fact value *is* the grounding, whatever the model called the outcome.

    Deliberately crude in one direction only. It can miss — a date reworded with no
    matching token reads as ungrounded, and the answer is then downgraded to an
    abstention, which is the safe failure. It cannot invent grounding that is not
    there, because every token it matches came out of the database row.
    """
    if not answer.strip():
        return False
    tokens = _fact_value_tokens(facts)
    if not tokens:
        return False
    return bool(tokens & fact_tokens(answer))


def enforce_grounding(
    outcome: str,
    answer: str,
    citations: list[dict[str, Any]],
    chunks: list[RetrievedChunk],
    *,
    facts: dict[str, Any] | None = None,
    used_product_facts: bool = False,
    conflict_positions: list[dict[str, Any]] | None = None,
) -> tuple[str, str, list[dict[str, Any]], bool]:
    """The last gate before an answer is shown or stored.

    The outcome is *derived here*, not taken from the model. The model's `outcome`
    field is treated as one more claim to be checked: what decides the outcome is
    what the server can verify — a validated cross-document conflict, a citation
    that resolves into this turn's context, or a fact value present in the text.
    Trusting the field instead produced the worst of both errors at once: an answer
    stating a correct expiry date, labelled `no_answer`, with the date left in the
    prose for the user to read under a heading saying nothing was found.

    Returns (outcome, answer, citations, used_product_facts).
    """
    has_facts = bool(facts)
    conflict_positions = conflict_positions or []
    text = answer.strip()

    if not chunks and not has_facts:
        # Nothing was retrieved and nothing is known about the product, so there is
        # genuinely nothing an answer could rest on, whatever the model said.
        return "no_answer", NO_ANSWER_TEXT, [], False

    # The record grounds the answer when a value out of it is present in the text —
    # that is checkable, and the model's own flag is not. The flag on its own counts
    # only next to a citation, where the citation does the grounding and the flag
    # merely says the record was also involved, so the app can label it.
    grounded_in_facts = has_facts and (
        facts_support(text, facts) or (used_product_facts and bool(citations))
    )

    if outcome == "conflicting_sources":
        # A conflict is a claim about two documents, so it has to be backed by two.
        documents = {entry["document_id"] for entry in conflict_positions}
        if len(conflict_positions) >= 2 and len(documents) >= 2:
            return (
                "conflicting_sources",
                render_conflict(text, conflict_positions),
                citations,
                grounded_in_facts,
            )
        logger.info(
            "conflicting_sources claimed with %s position(s) across %s document(s); downgraded",
            len(conflict_positions),
            len(documents),
        )

    if citations or grounded_in_facts:
        if not text:
            return "no_answer", NO_ANSWER_TEXT, [], False
        if outcome == "no_answer":
            # Self-reported abstention with verifiable grounding under it. The prose
            # is the answer; only the label was wrong.
            logger.info(
                "outcome=no_answer with %s citation(s) and facts=%s; promoted to answered",
                len(citations),
                grounded_in_facts,
            )
        return "answered", text, citations, grounded_in_facts

    # Nothing supports it: no passage from this turn, no value from the record. The
    # model's prose is discarded rather than shown — an uncited claim about someone's
    # warranty is exactly the output this gate exists to stop, and it is no safer for
    # having arrived under a `no_answer` label.
    logger.info("answer had no verifiable grounding; abstaining")
    return "no_answer", NO_ANSWER_TEXT, [], False


async def compose_answer(
    *,
    chat_model,
    question: str,
    chunks: list[RetrievedChunk],
    facts: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
) -> AnswerResult:
    """Ask the model for a grounded answer, then verify it is one."""
    if not chunks and not facts:
        # Skip the model call entirely: nothing was retrieved and nothing is known
        # about the product, so the only correct output is the abstention and paying
        # for a round-trip to be told that is waste. Facts alone are enough to
        # proceed — "how long is left on my warranty" has an exact answer that no
        # document could ever supply.
        return AnswerResult(outcome="no_answer", answer=NO_ANSWER_TEXT)

    blocks = [
        render_facts(facts),
        f"Sources:\n{render_sources(chunks)}",
        f"Question: {question}",
    ]
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
    conflict_positions = resolve_conflict_positions(parsed.get("conflicting_positions"), chunks)
    outcome, answer, citations, used_facts = enforce_grounding(
        outcome,
        answer,
        citations,
        chunks,
        facts=facts,
        used_product_facts=bool(parsed.get("used_product_facts")),
        conflict_positions=conflict_positions,
    )

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
        used_product_facts=used_facts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

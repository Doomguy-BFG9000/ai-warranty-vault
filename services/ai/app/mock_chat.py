"""A scripted chat model for offline development and tests.

This exists so the whole system — routing, the tool loop, citation validation,
the interrupt/resume cycle, the exactly-once approval — can be exercised without
a network call or an API key, and so tests assert on *behaviour* rather than on
what a real model happened to say that day.

It is not a quality stand-in. Selecting it requires `PROVIDER_MODE=mock`, it logs
a warning, and every surface that reports quality refuses to draw conclusions
from a mock run. A real provider is never replaced by this on failure.

Two ways to use it:

  * scripted — `push_response(...)` queues exact replies, which is how a test
    pins down "what happens when the model cites a chunk it was never given".
  * unscripted — a small deterministic heuristic that searches once and answers
    from the top chunk, which is what makes `PROVIDER_MODE=mock` a usable demo
    of the plumbing.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda

# Queue of canned replies. Each entry is either an AIMessage, or a dict for a
# structured-output call.
_SCRIPT: deque[Any] = deque()

# Narrow on purpose. A question that merely *mentions* service ("summarise the
# service appendix") is not a request to draft one, and a heuristic that cannot tell
# those apart would route half the question tests into the drafting branch.
_CLAIM_HINT = re.compile(
    r"(?:\b(?:przygotuj|napisz|sporz[ąa]d[źz]|utw[óo]rz|prepare|write|draft)\b"
    r"[^.?!]{0,80}?\b(?:zg[łl]oszen\w*|reklamacj\w*|pism\w*|wniosek|claim|request|complaint)\b)"
    r"|zg[łl]oszenie serwisowe"
    r"|\brma\b",
    re.IGNORECASE,
)


def push_response(response: Any) -> None:
    """Queue one reply. Consumed in FIFO order by the next model call."""
    _SCRIPT.append(response)


def push_responses(responses: Sequence[Any]) -> None:
    for response in responses:
        push_response(response)


def reset_script() -> None:
    _SCRIPT.clear()


def pending_responses() -> int:
    return len(_SCRIPT)


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model. See module docstring."""

    bound_tools: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-mock"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:
        clone = self.__class__()
        clone.bound_tools = list(tools)
        return clone

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable:
        """Structured output, answered from the script or by heuristic."""

        def _invoke(messages: Any) -> Any:
            if _SCRIPT:
                value = _SCRIPT.popleft()
                if isinstance(value, BaseException):
                    raise value
                return value
            return _heuristic_structured(schema, messages)

        return RunnableLambda(_invoke)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if _SCRIPT:
            value = _SCRIPT.popleft()
            if isinstance(value, BaseException):
                raise value
            message = value if isinstance(value, AIMessage) else AIMessage(content=str(value))
        else:
            message = _heuristic_turn(messages, self.bound_tools)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop, None, **kwargs)


# ── heuristics ──────────────────────────────────────────────────────────────
def _last_human_text(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(list(messages)):
        if message.type == "human":
            text = getattr(message, "text", None)
            return text if isinstance(text, str) else str(message.content)
    return ""


def _last_user_content(messages: Any) -> str:
    """The last user turn, whether the caller passed messages or role/content dicts.

    Both shapes occur: the agent node passes BaseMessages, the router and the
    answering step pass dicts. Falling back to the *whole* prompt here would be
    actively misleading — the system prompts mention "claim" and "draft", so a
    keyword heuristic run over them would classify every turn as a drafting request.
    """
    if hasattr(messages, "to_messages"):
        messages = messages.to_messages()
    if not isinstance(messages, (list, tuple)):
        return str(messages)

    for message in reversed(list(messages)):
        if isinstance(message, BaseMessage):
            if message.type == "human":
                text = getattr(message, "text", None)
                return text if isinstance(text, str) else str(message.content)
        elif isinstance(message, dict) and message.get("role") in ("user", "human"):
            return str(message.get("content", ""))
    return ""


def _schema_name(schema: dict[str, Any] | type) -> str:
    if isinstance(schema, dict):
        return str(schema.get("title") or schema.get("name") or "")
    return getattr(schema, "__name__", "")


def _as_text(messages: Any) -> str:
    """Flatten whatever was passed to the model into plain text.

    A real model reads the rendered prompt, so the heuristic does too — including the
    labelled sources block that `render_sources` produces. Accepts BaseMessages,
    role/content dicts and prompt values, because different call sites use different
    shapes.
    """
    if isinstance(messages, str):
        return messages
    if hasattr(messages, "to_messages"):
        messages = messages.to_messages()
    if not isinstance(messages, (list, tuple)):
        return str(messages)

    parts: list[str] = []
    for message in messages:
        if isinstance(message, BaseMessage):
            parts.append(str(message.content))
        elif isinstance(message, dict):
            parts.append(str(message.get("content", "")))
        else:
            parts.append(str(message))
    return "\n".join(parts)


_SOURCE_BLOCK_RE = re.compile(r"\[S(\d+)\][^\n]*\n(.*?)(?=\n\n\[S\d+\]|\n\nQuestion:|\n\nWhat the user|\Z)", re.S)


def _sources_in_prompt(text: str) -> list[tuple[str, str]]:
    """The labelled sources the caller put in the prompt, in order."""
    return [(f"S{number}", body.strip()) for number, body in _SOURCE_BLOCK_RE.findall(text)]


def _collect_tool_context(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """Chunk records from the tool messages already in the thread."""
    context: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(message.content if isinstance(message.content, str) else "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in payload.get("results", []) if isinstance(payload, dict) else []:
            if isinstance(item, dict) and item.get("chunk_id"):
                context.append(item)
    return context


def _heuristic_turn(messages: Sequence[BaseMessage], tools: Sequence[Any]) -> AIMessage:
    """Search once, then stop. Mirrors the shape of a real tool-using turn."""
    already_searched = any(isinstance(m, ToolMessage) for m in messages)
    tool_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    if not already_searched and "search_product_documents" in tool_names:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_product_documents",
                    "args": {"query": _last_human_text(messages)[:500]},
                    "id": "mock-call-1",
                }
            ],
        )
    return AIMessage(content="")


def _heuristic_structured(schema: dict[str, Any] | type, messages: Any) -> dict[str, Any]:
    name = _schema_name(schema).lower()
    text = _as_text(messages)
    question = _last_user_content(messages)

    if "transcription" in name:
        # Deliberately empty. Fabricating a transcription would invent the contents
        # of somebody's document, and the caller correctly treats "no pages" as an
        # unreadable document. A test that needs the OCR path scripts a reply.
        return {"pages": []}

    if "route" in name or "intent" in name:
        return {
            "intent": "claim_draft" if _CLAIM_HINT.search(question) else "question",
            "needs_clarification": False,
            "clarifying_question": "",
            "reason": "mock heuristic",
        }

    if "answer" in name:
        sources = _sources_in_prompt(text)
        if not sources:
            return {
                "outcome": "no_answer",
                "answer": "The attached documents do not contain an answer to this question.",
                "cited_sources": [],
            }
        label, body = sources[0]
        return {"outcome": "answered", "answer": body[:400], "cited_sources": [label]}

    if "draft" in name or "claim" in name:
        sources = _sources_in_prompt(text)
        return {
            "subject": "Service request",
            "body": (
                "Hello,\n\nI am reporting a fault with the product listed below and "
                "asking for it to be handled under warranty.\n\n"
                "Please confirm the next steps.\n\nKind regards"
            ),
            "cited_sources": [sources[0][0]] if sources else [],
            "missing_information": [],
        }

    return {}

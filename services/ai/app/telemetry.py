"""Operation logging.

What gets recorded, and what deliberately does not.

Recorded: an operation id that ties every stage of one user action together, the
stage name, how long it took, which model and prompt version ran, the provider's
own token counts when it reports them, small bounded counters (how many chunks,
what top-k), and an error code on failure.

Not recorded, anywhere, at any level: API keys, user access tokens, prompts,
document text, chunk text, question text, or answers. The log is meant to be
readable by whoever is on call without that being a privacy incident, and a
document's contents are the user's private paperwork. When a retrieval problem
needs the actual text, that is what the developer trace endpoint is for — it runs
under the caller's own token and shows only their own documents.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai.telemetry")

_operation_id: ContextVar[uuid.UUID | None] = ContextVar("operation_id", default=None)

# Substrings that must never appear in a telemetry `detail` payload. Cheap
# belt-and-braces against a future caller passing the wrong dict in.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "content", "text", "prompt", "question", "answer", "token", "key",
        "authorization", "jwt", "body", "chunk_text", "quote",
    }
)


def new_operation_id() -> uuid.UUID:
    op = uuid.uuid4()
    _operation_id.set(op)
    return op


def current_operation_id() -> uuid.UUID:
    op = _operation_id.get()
    if op is None:
        op = new_operation_id()
    return op


def _clean_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return {}
    out: dict[str, Any] = {}
    for key, value in detail.items():
        lowered = key.lower()
        if any(bad in lowered for bad in _FORBIDDEN_DETAIL_KEYS):
            # Dropped rather than truncated: a truncated prompt is still a prompt.
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            # Short enum-ish strings only (statuses, modes, mime types).
            out[key] = value[:64]
        elif isinstance(value, (list, tuple)):
            out[key] = len(value)
    return out


@dataclass
class StageRecord:
    stage: str
    status: str = "ok"
    model: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None


class Telemetry:
    """Writes stage records. `db` may be None in unit tests and offline tools."""

    def __init__(self, db: Any | None = None) -> None:
        self._db = db

    @asynccontextmanager
    async def stage(
        self,
        name: str,
        *,
        user_id: uuid.UUID | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        **detail: Any,
    ):
        record = StageRecord(stage=name, model=model, prompt_version=prompt_version, detail=detail)
        started = time.perf_counter()
        try:
            yield record
        except Exception as exc:  # noqa: BLE001 - re-raised below
            record.status = "error"
            record.error_code = getattr(exc, "code", None) or type(exc).__name__
            raise
        finally:
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            await self.write(record, user_id=user_id)

    async def write(self, record: StageRecord, *, user_id: uuid.UUID | None = None) -> None:
        operation_id = current_operation_id()
        detail = _clean_detail(record.detail)

        logger.info(
            "stage=%s status=%s ms=%s model=%s op=%s",
            record.stage,
            record.status,
            record.duration_ms,
            record.model,
            operation_id,
            extra={"detail": detail, "error_code": record.error_code},
        )

        if self._db is None:
            return

        try:
            async with self._db.service_scope() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        insert into public.ai_operation_log
                          (operation_id, user_id, stage, status, duration_ms, model,
                           prompt_version, input_tokens, output_tokens, detail, error_code)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            operation_id,
                            user_id,
                            record.stage,
                            record.status,
                            record.duration_ms,
                            record.model,
                            record.prompt_version,
                            record.input_tokens,
                            record.output_tokens,
                            json.dumps(detail),
                            record.error_code,
                        ),
                    )
        except Exception:  # noqa: BLE001
            # Telemetry must never be the reason a user action fails.
            logger.warning("failed to persist telemetry for stage=%s", record.stage, exc_info=False)

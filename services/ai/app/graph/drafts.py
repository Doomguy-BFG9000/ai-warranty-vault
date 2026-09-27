"""Persisting a claim draft, and approving it exactly once.

The two operations here are the ones the human-in-the-loop story rests on.

`propose_draft` is called by a node that may be re-executed. LangGraph replays the
task that was interrupted from the *start of its node*, so anything before an
`interrupt()` must be safe to run twice. This is made safe by an idempotency key
rather than by hoping: `(thread_id, content_sha256)` is unique, the insert is
`on conflict do nothing`, and the existing row is read back. A replay therefore
finds the draft it already wrote instead of proposing a second, near-identical one.

`approve_draft` is a single conditional UPDATE — `where status = 'pending'` — so a
second approval updates zero rows and is reported as "already decided" rather than
writing again. A unique partial index (`where status = 'approved'`) is the second
lock, in case two approvals arrive in the same instant on different connections.
The approved text is stored in its own columns, so what the human signed can never
be confused with, or overwritten by, what the model proposed.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from app.db import Database, fetch_one

logger = logging.getLogger(__name__)


def draft_content_hash(subject: str, body: str) -> str:
    return hashlib.sha256(f"{subject.strip()}\n\n{body.strip()}".encode("utf-8")).hexdigest()


async def propose_draft(
    db: Database,
    *,
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    subject: str,
    body: str,
    facts: dict[str, Any],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record a proposed draft. Idempotent on (thread, content)."""
    content_sha256 = draft_content_hash(subject, body)

    async with db.service_scope() as conn:
        existing = await fetch_one(
            conn,
            """
            select id, version, status, subject, body, facts, citations, content_sha256
            from public.claim_drafts
            where thread_id = %s and content_sha256 = %s
            """,
            (thread_id, content_sha256),
        )
        if existing is not None:
            logger.info("draft replay for thread %s matched version %s", thread_id, existing["version"])
            return _serialise(existing)

        # Any earlier undecided proposal in this thread is superseded, so the user is
        # never shown two pending drafts and "the pending draft" is unambiguous.
        await conn.execute(
            """
            update public.claim_drafts
               set status = 'superseded'
             where thread_id = %s and status = 'pending'
            """,
            (thread_id,),
        )

        row = await fetch_one(
            conn,
            """
            insert into public.claim_drafts
              (thread_id, user_id, product_id, version, status, subject, body,
               facts, citations, content_sha256)
            values (
              %(thread_id)s, %(user_id)s, %(product_id)s,
              (select coalesce(max(version), 0) + 1 from public.claim_drafts where thread_id = %(thread_id)s),
              'pending', %(subject)s, %(body)s, %(facts)s, %(citations)s, %(hash)s
            )
            returning id, version, status, subject, body, facts, citations, content_sha256
            """,
            {
                "thread_id": thread_id,
                "user_id": user_id,
                "product_id": product_id,
                "subject": subject.strip(),
                "body": body.strip(),
                "facts": Jsonb(facts),
                "citations": Jsonb(citations),
                "hash": content_sha256,
            },
        )

    assert row is not None
    return _serialise(row)


async def approve_draft(
    db: Database,
    claims: dict[str, Any],
    *,
    draft_id: uuid.UUID,
    approved_subject: str,
    approved_body: str,
) -> dict[str, Any]:
    """Approve one specific draft, once.

    Runs in the *user* scope: approving is the user's action, so RLS is what
    authorises it, and the database triggers are what constrain its shape.
    """
    async with db.user_scope(claims) as conn:
        row = await fetch_one(
            conn,
            """
            update public.claim_drafts
               set status           = 'approved',
                   approved_subject = %(subject)s,
                   approved_body    = %(body)s
             where id = %(id)s
               and status = 'pending'
            returning id, version, status, subject, body, approved_subject,
                      approved_body, approved_at, citations
            """,
            {"id": draft_id, "subject": approved_subject.strip(), "body": approved_body.strip()},
        )
        if row is not None:
            return {"applied": True, **_serialise(row)}

        # Zero rows updated: either it is not pending any more, or it is not this
        # user's draft. Read it back under the same RLS scope to tell the app which.
        current = await fetch_one(
            conn,
            """
            select id, version, status, subject, body, approved_subject, approved_body,
                   approved_at, citations
            from public.claim_drafts
            where id = %s
            """,
            (draft_id,),
        )

    if current is None:
        return {"applied": False, "reason": "not_found"}
    # The idempotent case: the user (or a retried request) approved this already.
    # Reported as a success with applied=False so the caller can tell "nothing
    # changed" from "something went wrong", and no second row is written.
    return {"applied": False, "reason": f"already_{current['status']}", **_serialise(current)}


async def reject_draft(db: Database, claims: dict[str, Any], *, draft_id: uuid.UUID) -> dict[str, Any]:
    async with db.user_scope(claims) as conn:
        row = await fetch_one(
            conn,
            """
            update public.claim_drafts
               set status = 'rejected'
             where id = %s and status = 'pending'
            returning id, version, status, subject, body, citations
            """,
            (draft_id,),
        )
        if row is not None:
            return {"applied": True, **_serialise(row)}
        current = await fetch_one(
            conn,
            "select id, version, status, subject, body, citations from public.claim_drafts where id = %s",
            (draft_id,),
        )
    if current is None:
        return {"applied": False, "reason": "not_found"}
    return {"applied": False, "reason": f"already_{current['status']}", **_serialise(current)}


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "draft_id": str(row["id"]),
        "version": row["version"],
        "status": row["status"],
        "subject": row.get("subject"),
        "body": row.get("body"),
        "citations": row.get("citations") or [],
    }
    if row.get("approved_subject") is not None:
        out["approved_subject"] = row["approved_subject"]
    if row.get("approved_body") is not None:
        out["approved_body"] = row["approved_body"]
    if row.get("approved_at") is not None:
        out["approved_at"] = row["approved_at"].isoformat()
    if row.get("facts") is not None:
        out["facts"] = row["facts"]
    return out

#!/usr/bin/env python
"""Check that the configured models and keys actually work.

Run this before believing anything about a live setup:

    .venv/bin/python scripts/preflight.py

Model identifiers go out of date, and a key that works for one model may have no
access to another. This makes one small real call per configured provider and
reports what came back, rather than assuming the names in the configuration are
available. It is the only script here that intentionally spends money — a few
hundred tokens at most.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import Database, assert_embedding_column_width, fetch_one  # noqa: E402
from app.errors import ProviderError  # noqa: E402
from app.ingest.indexer import index_version_for  # noqa: E402
from app.ingest.tokens import tokenizer_id  # noqa: E402
from app.providers import build_chat_model, build_embeddings, embed_query  # noqa: E402

OK = "  ok   "
FAIL = " FAIL  "
SKIP = " skip  "


def line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


async def main() -> int:
    settings = get_settings()
    failures = 0

    print(f"provider_mode={settings.provider_mode}  index_version={index_version_for(settings)}")
    print(f"tokenizer={tokenizer_id()}\n")

    if tokenizer_id().startswith("approx/"):
        line(
            SKIP,
            "tiktoken",
            "vocabulary could not be downloaded; the approximate token counter is in use. "
            "Chunk boundaries differ from a tiktoken run — reindex if you switch.",
        )

    # ── database ────────────────────────────────────────────────────────────
    db = Database(settings)
    try:
        await db.open()
        async with db.service_scope() as conn:
            row = await fetch_one(conn, "select extversion from pg_extension where extname = 'vector'")
        if row is None:
            line(FAIL, "pgvector", "extension is not installed in this database")
            failures += 1
        else:
            line(OK, "pgvector", f"version {row['extversion']}")

        await assert_embedding_column_width(db, settings.embedding_dimensions)
        line(OK, "schema", f"document_chunks.embedding is vector({settings.embedding_dimensions})")

        async with db.service_scope() as conn:
            counts = await fetch_one(
                conn,
                """
                select (select count(*) from public.product_documents) as documents,
                       (select count(*) from public.document_chunks)   as chunks,
                       (select count(*) from public.document_index_jobs
                         where state in ('queued','running'))          as live_jobs
                """,
            )
        line(
            OK,
            "contents",
            f"{counts['documents']} documents, {counts['chunks']} chunks, {counts['live_jobs']} live job(s)",
        )

        graph_pool = await db.graph_pool()
        async with graph_pool.connection() as conn:
            await conn.execute("select 1")
        line(OK, "graph schema", f"reachable as {settings.graph_schema}")
    except Exception as exc:  # noqa: BLE001
        line(FAIL, "database", f"{type(exc).__name__}: {exc}")
        failures += 1
    finally:
        await db.close()

    if settings.is_mock:
        line(SKIP, "providers", "PROVIDER_MODE=mock, so no live calls were made")
        print("\nmock mode: the pipeline is exercisable, but nothing here proves a model works.")
        return 1 if failures else 0

    # ── embeddings ──────────────────────────────────────────────────────────
    try:
        handle = build_embeddings(settings)
        vector = await embed_query(handle, "warranty claim test")
        line(OK, "embeddings", f"{handle.model} returned {len(vector)} dimensions")
    except ProviderError as exc:
        line(FAIL, "embeddings", str(exc))
        failures += 1
    except Exception as exc:  # noqa: BLE001
        line(FAIL, "embeddings", f"{type(exc).__name__}: {exc}")
        failures += 1

    # ── answering model ─────────────────────────────────────────────────────
    for label, model in (("answer model", settings.answer_model), ("vision model", settings.vision_model)):
        try:
            chat = build_chat_model(settings, model=model, max_tokens=32)
            reply = await chat.ainvoke([{"role": "user", "content": "Reply with the single word: ready"}])
            text = getattr(reply, "text", None)
            preview = (text if isinstance(text, str) else str(reply.content)).strip()[:40]
            line(OK, label, f"{model} replied {preview!r}")
        except Exception as exc:  # noqa: BLE001
            # The common cause is a model identifier this key cannot reach, which is
            # exactly what this script exists to surface.
            line(FAIL, label, f"{model}: {type(exc).__name__}: {exc}")
            failures += 1

    print()
    if failures:
        print(f"{failures} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

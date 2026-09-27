"""Database access, in two deliberately unequal scopes.

The whole authorization story of this service is in this file.

**User scope** (`user_scope`) is how every request-driven query runs. It opens a
transaction and then does what PostgREST does:

    select set_config('role', 'authenticated', true);
    select set_config('request.jwt.claims', '<verified claims>', true);

From that point the connection is an ordinary `authenticated` session and Row
Level Security is in force — including inside `search_document_chunks`, which is
SECURITY INVOKER precisely so the owner predicate becomes part of the scan that
produces the top-k. The claims come from a *verified* token (app/auth.py); no
code path anywhere puts a request-supplied user id into them.

**Service scope** (`service_scope`) is the escape hatch, and it is small on
purpose. Three things genuinely have no caller:

  * the indexing worker, which runs minutes after the request that queued it and
    cannot borrow an expired JWT;
  * server-authored rows — assistant turns, draft proposals, indexing state —
    which exist precisely so a client cannot forge them;
  * the telemetry log, which clients have no access to at all.

Every service-scope write is reached through a function that was handed ids
already read under user scope, so the ownership decision is always made by RLS
even when the write is not. `tests/test_access_isolation.py` asserts that the
user scope cannot perform those writes, which is what keeps this boundary from
quietly widening.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# pgvector lives in the `extensions` schema on Supabase. Both schemas are on the
# search path so `<=>` and the `vector` type resolve unqualified, matching the
# project's PostgREST configuration.
_SEARCH_PATH = "public, extensions"


async def _configure(conn: psycopg.AsyncConnection[Any]) -> None:
    await conn.set_autocommit(True)
    async with conn.cursor() as cur:
        await cur.execute(f"set search_path to {_SEARCH_PATH}")
    # Teaches psycopg to adapt Python lists to `vector` and back. Without it
    # every embedding would have to be hand-formatted into pgvector's text
    # syntax at the call site.
    await register_vector_async(conn)
    await conn.set_autocommit(False)


class Database:
    """Owns the connection pools and hands out the two scopes."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pool: AsyncConnectionPool | None = None
        self._graph_pool: AsyncConnectionPool | None = None

    async def open(self) -> None:
        if self._pool is not None:
            return
        self._pool = AsyncConnectionPool(
            self._settings.database_url,
            min_size=self._settings.db_pool_min,
            max_size=self._settings.db_pool_max,
            kwargs={"row_factory": dict_row, "autocommit": False},
            configure=_configure,
            open=False,
        )
        await self._pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        if self._graph_pool is not None:
            await self._graph_pool.close()
            self._graph_pool = None
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Database.open() was not awaited")
        return self._pool

    async def graph_pool(self) -> AsyncConnectionPool:
        """Autocommit pool scoped to the LangGraph schema.

        The checkpointer manages its own transactions and creates its tables on
        first use, so it gets a pool of its own with `search_path` pinned to
        `ai_graph`. Nothing else uses this pool, and `authenticated` has no
        privileges on that schema at all.
        """
        if self._graph_pool is None:
            schema = self._settings.graph_schema

            async def _configure_graph(conn: psycopg.AsyncConnection[Any]) -> None:
                async with conn.cursor() as cur:
                    await cur.execute(f'set search_path to "{schema}", public')

            self._graph_pool = AsyncConnectionPool(
                self._settings.database_url,
                min_size=1,
                max_size=max(2, self._settings.db_pool_max // 2),
                kwargs={"row_factory": dict_row, "autocommit": True},
                configure=_configure_graph,
                open=False,
            )
            await self._graph_pool.open(wait=True, timeout=30)
        return self._graph_pool

    # ── scopes ──────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def user_scope(self, claims: dict[str, Any]) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """A transaction that RLS governs, on behalf of a verified caller.

        `claims` must be the output of token verification. A `sub` is mandatory:
        without it `auth.uid()` is null, every policy evaluates false, and the
        caller would silently see nothing instead of being rejected — so the
        missing-subject case is an error here rather than an empty result later.
        """
        subject = claims.get("sub")
        if not subject:
            raise ValueError("user_scope requires verified claims containing 'sub'")

        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select set_config('role', %s, true),"
                        "       set_config('request.jwt.claims', %s, true),"
                        "       set_config('statement_timeout', %s, true)",
                        (
                            "authenticated",
                            json.dumps(claims, separators=(",", ":")),
                            str(self._settings.db_statement_timeout_ms),
                        ),
                    )
                yield conn

    @asynccontextmanager
    async def service_scope(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """A transaction that bypasses RLS. Keep the body short and specific.

        The claims are cleared explicitly. Connections are pooled, and a custom GUC
        that was set transaction-locally reverts to the *empty string* rather than to
        unset — so without this, a service-scope transaction on a recycled connection
        could observe a leftover value. Clearing it means `auth.uid()` is reliably
        null here, which is what tells the free-tier trigger and the column guards
        that they are looking at server-side work rather than a user request.
        """
        async with self.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select set_config('role', %s, true),"
                        "       set_config('request.jwt.claims', '', true),"
                        "       set_config('statement_timeout', %s, true)",
                        (
                            self._settings.db_privileged_role,
                            str(self._settings.db_statement_timeout_ms),
                        ),
                    )
                yield conn


async def fetch_all(conn: psycopg.AsyncConnection[Any], sql: str, params: Any = None) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def fetch_one(conn: psycopg.AsyncConnection[Any], sql: str, params: Any = None) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def execute(conn: psycopg.AsyncConnection[Any], sql: str, params: Any = None) -> int:
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return cur.rowcount


async def assert_embedding_column_width(db: Database, expected: int) -> None:
    """Refuse to run against a schema whose vector column is a different width.

    Writing 1024-dimension vectors into a 1536-wide column (or the reverse) is
    the kind of mistake that only shows up as mysteriously bad answers, so the
    service checks the actual column type at startup and stops instead.
    """
    async with db.service_scope() as conn:
        row = await fetch_one(
            conn,
            """
            select a.atttypmod as typmod
            from pg_attribute a
            join pg_class c on c.oid = a.attrelid
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public'
              and c.relname = 'document_chunks'
              and a.attname = 'embedding'
            """,
        )
    if row is None:
        raise RuntimeError(
            "public.document_chunks.embedding is missing — apply the Supabase migrations first"
        )
    actual = int(row["typmod"])
    if actual != expected:
        raise RuntimeError(
            f"embedding column is vector({actual}) but EMBEDDING_DIMENSIONS={expected}. "
            "Change the setting back, or write a migration that alters the column width "
            "and reindex every document."
        )

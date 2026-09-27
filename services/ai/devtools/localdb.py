"""Apply the project's real migrations to a local Postgres.

Used by the test suite and the evaluation harness. The point is that tests run
against *the SQL that ships* — the same files `supabase db reset` applies — rather
than against a hand-written test schema that could drift from production and hide
an RLS mistake.

Two files are handled specially:

  * `tests/sql/supabase_shim.sql` runs first and provides what Supabase would:
    the roles, `auth.uid()`, an `auth.users` table, and enough of `storage` for the
    bucket policies to exist.
  * the pg_cron/pg_net migration is skipped, because those extensions are not
    available in a bare Postgres and nothing in the document assistant depends on
    them. Everything else, including every RLS policy and every trigger, is
    applied verbatim.

Requires pgvector. On Debian/Ubuntu: `apt-get install postgresql-16-pgvector`.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re

import psycopg

logger = logging.getLogger(__name__)

SERVICE_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
SHIM_SQL = SERVICE_ROOT / "tests" / "sql" / "supabase_shim.sql"

# Needs pg_cron and pg_net, which a bare Postgres does not have. It only schedules
# reminder delivery and is unrelated to everything under test here.
SKIPPED_MIGRATIONS = frozenset({"20260814000005_reminder_schedule.sql"})

DEFAULT_ADMIN_DSN = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/postgres"
)
DEFAULT_TEST_DB = os.environ.get("TEST_DATABASE_NAME", "ai_vault_test")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def test_database_url(database: str | None = None) -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    name = database or DEFAULT_TEST_DB
    base = DEFAULT_ADMIN_DSN.rsplit("/", 1)[0]
    return f"{base}/{name}"


def migration_files() -> list[pathlib.Path]:
    return [
        path
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if path.name not in SKIPPED_MIGRATIONS
    ]


def create_database(name: str | None = None, *, admin_dsn: str | None = None) -> str:
    """Create the test database if it is not there. Returns its DSN."""
    name = name or DEFAULT_TEST_DB
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe database name: {name!r}")

    with psycopg.connect(admin_dsn or DEFAULT_ADMIN_DSN, autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(f'create database "{name}"')
            logger.info("created test database %s", name)
    return test_database_url(name)


def reset_schema(dsn: str) -> None:
    """Drop everything this project owns and rebuild it from the migrations."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        # Order matters only in that `public` has to come back before the
        # migrations run; cascade takes care of dependencies.
        for schema in ("ai_graph", "storage", "auth", "public", "extensions"):
            conn.execute(f"drop schema if exists {schema} cascade")
        conn.execute("create schema public")
        conn.execute("grant usage on schema public to public")

        _run_file(conn, SHIM_SQL)
        for path in migration_files():
            _run_file(conn, path)

    logger.info("applied %s migrations to %s", len(migration_files()), dsn.rsplit("/", 1)[-1])


def _run_file(conn: psycopg.Connection, path: pathlib.Path) -> None:
    sql = path.read_text(encoding="utf-8")
    try:
        conn.execute(sql)
    except psycopg.Error as exc:
        raise RuntimeError(f"failed while applying {path.name}: {exc}") from exc


def ensure_test_database(*, force: bool = False) -> str:
    """Create-and-reset in one call. Idempotent."""
    dsn = create_database()
    if force or not _schema_present(dsn):
        reset_schema(dsn)
    return dsn


def _schema_present(dsn: str) -> bool:
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "select 1 from information_schema.tables where table_schema = 'public' and table_name = 'document_chunks'"
        ).fetchone()
        return row is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(ensure_test_database(force=True))

"""Test helpers: a fake object store, and a shortcut to an indexed document.

The fake store replaces only the HTTP call to Supabase Storage. Everything else —
extraction, chunking, embedding, the write transaction, the job queue — is the real
code path, so an indexing test exercises the thing that runs in production rather
than a reimplementation of it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.config import Settings
from app.db import Database, fetch_one
from app.ingest.indexer import index_document
from app.providers import EmbeddingHandle
from app.storage import StoredObject
from app.telemetry import Telemetry


@dataclass
class FakeStore:
    """In-memory stand-in for the private bucket, keyed by object path."""

    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    readable: set[str] = field(default_factory=set)
    deleted: list[str] = field(default_factory=list)
    # Set to raise from the download, to test the worker's retry policy.
    fail_with: Exception | None = None

    def put(self, path: str, data: bytes, content_type: str = "text/plain") -> None:
        self.objects[path] = (data, content_type)
        self.readable.add(path)

    async def download(self, _settings, path: str, *, bucket: str = "", max_bytes: int | None = None) -> StoredObject:
        if self.fail_with is not None:
            raise self.fail_with
        if path not in self.objects:
            from app.errors import ProviderError

            raise ProviderError("the stored file is gone", code="object_missing", retryable=False)
        data, content_type = self.objects[path]
        return StoredObject(data=data, content_type=content_type, byte_size=len(data))

    async def verify(self, _settings, path: str, _token: str, *, bucket: str = "") -> bool:
        return path in self.readable

    async def delete(self, _settings, path: str, _token: str, *, bucket: str = "") -> None:
        self.deleted.append(path)
        self.objects.pop(path, None)
        self.readable.discard(path)


def object_path(user_id: uuid.UUID, product_id: uuid.UUID, name: str) -> str:
    return f"{user_id}/{product_id}/{name}"


async def register_document_row(
    db: Database,
    *,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    title: str,
    storage_path: str,
    mime_type: str = "text/plain",
    byte_size: int = 1024,
    kind: str = "manual",
) -> uuid.UUID:
    async with db.service_scope() as conn:
        row = await fetch_one(
            conn,
            """
            insert into public.product_documents
              (product_id, user_id, title, kind, storage_path, mime_type, byte_size)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (product_id, user_id, title, kind, storage_path, mime_type, byte_size),
        )
    assert row is not None
    return row["id"]


async def seed_indexed_document(
    *,
    db: Database,
    settings: Settings,
    embeddings: EmbeddingHandle,
    telemetry: Telemetry,
    store: FakeStore,
    user_id: uuid.UUID,
    product_id: uuid.UUID,
    title: str,
    text: str,
    mime_type: str = "text/plain",
    kind: str = "manual",
    filename: str | None = None,
) -> uuid.UUID:
    """Put a document in the fake store, register it, and index it for real."""
    name = filename or f"{uuid.uuid4().hex[:8]}.txt"
    path = object_path(user_id, product_id, name)
    data = text.encode("utf-8")
    store.put(path, data, mime_type)

    document_id = await register_document_row(
        db,
        user_id=user_id,
        product_id=product_id,
        title=title,
        storage_path=path,
        mime_type=mime_type,
        byte_size=len(data),
        kind=kind,
    )
    await index_document(
        document_id, db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
    )
    return document_id


async def chunk_count(db: Database, document_id: uuid.UUID) -> int:
    async with db.service_scope() as conn:
        row = await fetch_one(
            conn,
            "select count(*) as n from public.document_chunks where document_id = %s",
            (document_id,),
        )
    return int((row or {}).get("n", 0))


async def chunk_ids(db: Database, document_id: uuid.UUID) -> list[uuid.UUID]:
    async with db.service_scope() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id from public.document_chunks where document_id = %s order by chunk_index",
                (document_id,),
            )
            return [row["id"] for row in await cur.fetchall()]


class expect_sqlstate:
    """Assert that a block fails with one of the given Postgres SQLSTATEs.

    Matching on SQLSTATE rather than on the psycopg exception class, because the
    custom `PTxyz` codes this project raises (so PostgREST maps them to an HTTP
    status) land in psycopg's generic `ProgrammingError` — asserting on the class
    would pass for almost any SQL mistake.
    """

    def __init__(self, *sqlstates: str) -> None:
        self.expected = set(sqlstates)
        self.error: Exception | None = None

    def __enter__(self) -> "expect_sqlstate":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is None:
            raise AssertionError(f"expected SQLSTATE in {sorted(self.expected)}, but nothing was raised")
        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate not in self.expected:
            raise AssertionError(
                f"expected SQLSTATE in {sorted(self.expected)}, got {sqlstate}: {exc}"
            ) from exc
        self.error = exc
        return True


# Codes this project's guards and policies raise.
FORBIDDEN = "PT403"
CONFLICT = "PT409"
UNPROCESSABLE = "PT422"
RLS_VIOLATION = "42501"  # insufficient_privilege, also raised by a failed WITH CHECK
CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"


def build_text_pdf(pages: list[str]) -> bytes:
    """A minimal multi-page PDF with a real text layer.

    Written by hand rather than with a PDF library so the test suite needs no extra
    dependency, and so the fixture is a genuine PDF that pypdf parses the same way it
    parses a real one. Text is ASCII: the standard Helvetica encoding would mangle
    diacritics, and this fixture is about page *provenance*, not about character sets.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # 1-based object number

    font_number = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")

    page_numbers: list[int] = []
    pages_number = len(objects) + 1 + 2 * len(pages)  # reserved below

    for text in pages:
        lines = text.splitlines() or [""]
        commands = ["BT", "/F1 12 Tf", "50 780 Td", "16 TL"]
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            commands.append(f"({escaped}) Tj T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1")
        content_number = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page_numbers.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_number, font_number, content_number)
            )
        )

    kids = b" ".join(b"%d 0 R" % number for number in page_numbers)
    actual_pages_number = add(
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_numbers))
    )
    assert actual_pages_number == pages_number, "reserved /Pages object number drifted"
    catalog_number = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_number)

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_offset = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_number,
        xref_offset,
    )
    return bytes(out)

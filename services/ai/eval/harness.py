"""Shared setup for the evaluation: a database, two users, and the indexed corpus.

Kept separate from `run_eval.py` so the same seeding can be reused from a REPL or a
notebook when investigating a single bad retrieval.
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
from eval.corpus import DOCUMENTS


@dataclass
class EvalUser:
    user_id: uuid.UUID
    products: dict[str, uuid.UUID] = field(default_factory=dict)

    @property
    def claims(self) -> dict[str, str]:
        return {"sub": str(self.user_id), "role": "authenticated", "aud": "authenticated"}


@dataclass
class EvalWorld:
    owner: EvalUser
    outsider: EvalUser
    documents: dict[str, uuid.UUID]


class _MemoryStore:
    """Stands in for Supabase Storage. Only the HTTP fetch is replaced."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, path: str, data: bytes) -> None:
        self.objects[path] = data

    async def download(self, _settings, path: str, *, bucket: str = "", max_bytes: int | None = None) -> StoredObject:
        data = self.objects[path]
        return StoredObject(data=data, content_type="text/plain", byte_size=len(data))


async def _make_user(db: Database, email: str) -> EvalUser:
    async with db.service_scope() as conn:
        row = await fetch_one(conn, "insert into auth.users (email) values (%s) returning id", (email,))
        user_id = row["id"]
        await conn.execute(
            "insert into public.profiles (id, email) values (%s, %s) on conflict do nothing",
            (user_id, email),
        )
        products: dict[str, uuid.UUID] = {}
        for key, name, months in (
            ("washer", "AquaWash 8000 washing machine", 24),
            ("coffee", "BrewMaster 300 espresso machine", 12),
        ):
            product = await fetch_one(
                conn,
                """
                insert into public.products
                  (user_id, name, merchant, purchase_date, warranty_months, price_cents, currency, category)
                values (%s, %s, 'TechWorld', '2025-03-14', %s, 249900, 'PLN', 'appliance')
                returning id
                """,
                (user_id, name, months),
            )
            products[key] = product["id"]
    return EvalUser(user_id=user_id, products=products)


async def build_world(
    *, db: Database, settings: Settings, embeddings: EmbeddingHandle, telemetry: Telemetry
) -> EvalWorld:
    """Seed the corpus for one owner, plus an outsider who owns none of it.

    The outsider is what makes the access questions in the label set mean anything:
    the phrases they must *not* retrieve do exist in the database, just not in their
    account.
    """
    from unittest.mock import patch

    from app.ingest import indexer as indexer_module

    owner = await _make_user(db, f"owner-{uuid.uuid4().hex[:6]}@eval.test")
    outsider = await _make_user(db, f"outsider-{uuid.uuid4().hex[:6]}@eval.test")

    store = _MemoryStore()
    documents: dict[str, uuid.UUID] = {}

    with patch.object(indexer_module, "download_as_service", store.download):
        for document in DOCUMENTS:
            product_id = owner.products[document.product]
            path = f"{owner.user_id}/{product_id}/{document.slug}.txt"
            data = document.text.encode("utf-8")
            store.put(path, data)

            async with db.service_scope() as conn:
                row = await fetch_one(
                    conn,
                    """
                    insert into public.product_documents
                      (product_id, user_id, title, kind, storage_path, mime_type, byte_size)
                    values (%s, %s, %s, %s, %s, 'text/plain', %s)
                    returning id
                    """,
                    (product_id, owner.user_id, document.title, document.kind, path, len(data)),
                )
            documents[document.slug] = row["id"]
            await index_document(
                row["id"], db=db, settings=settings, embeddings=embeddings, telemetry=telemetry
            )

    return EvalWorld(owner=owner, outsider=outsider, documents=documents)

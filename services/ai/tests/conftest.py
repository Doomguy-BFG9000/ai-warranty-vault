"""Test fixtures.

Everything runs against a real local Postgres with pgvector and the project's real
migrations — RLS policies, triggers, the search function, the lot. A mocked
database would not be able to tell whether `search_document_chunks` filters before
or after the top-k, and that is one of the properties most worth testing.

Two users exist for the whole session and both own products and documents. Tests
therefore always query a database that contains someone else's rows, which is the
only arrangement in which an isolation test means anything.

Providers are in explicit mock mode (`PROVIDER_MODE=mock`). That exercises the
plumbing, the SQL, the graph and the citation rules without a network call. It says
nothing about embedding quality — that is what `eval/` is for, and it refuses to
report quality numbers from mock mode.
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

SERVICE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

from devtools.localdb import ensure_test_database  # noqa: E402

TEST_DSN = ensure_test_database(force=True)

# Set before anything imports app.config, whose Settings are cached.
os.environ.update(
    {
        "DATABASE_URL": TEST_DSN,
        "PROVIDER_MODE": "mock",
        "SUPABASE_URL": "http://127.0.0.1:54321",
        "SUPABASE_JWT_SECRET": "test-jwt-secret-not-a-real-one-padded-to-32b",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
        "WORKER_LEASE_SECONDS": "5",
        "WORKER_BACKOFF_BASE_SECONDS": "1",
        "WORKER_POLL_SECONDS": "0.05",
        # Small chunks so a short fixture document produces several of them.
        "CHUNK_TARGET_TOKENS": "60",
        "CHUNK_OVERLAP_TOKENS": "12",
        "CHUNK_MIN_TOKENS": "6",
    }
)

from app.config import Settings, get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.providers import build_embeddings  # noqa: E402
from app.telemetry import Telemetry  # noqa: E402


@dataclass
class SeededUser:
    user_id: uuid.UUID
    email: str
    product_id: uuid.UUID
    second_product_id: uuid.UUID

    @property
    def claims(self) -> dict[str, Any]:
        return {"sub": str(self.user_id), "role": "authenticated", "aud": "authenticated"}


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
async def db(settings: Settings):
    database = Database(settings)
    await database.open()
    yield database
    await database.close()


@pytest.fixture(scope="session")
def embeddings(settings: Settings):
    return build_embeddings(settings)


@pytest.fixture(scope="session")
def telemetry(db: Database) -> Telemetry:
    return Telemetry(db)


async def _create_user(db: Database, email: str) -> SeededUser:
    async with db.service_scope() as conn:
        row = await (
            await conn.execute("insert into auth.users (email) values (%s) returning id", (email,))
        ).fetchone()
        user_id = row["id"]
        await conn.execute(
            "insert into public.profiles (id, email) values (%s, %s) on conflict do nothing",
            (user_id, email),
        )
        products = []
        for name, months in (("AquaWash 8000 washing machine", 24), ("BrewMaster coffee machine", 12)):
            product = await (
                await conn.execute(
                    """
                    insert into public.products (user_id, name, merchant, purchase_date, warranty_months,
                                                 price_cents, currency, category)
                    values (%s, %s, 'TechWorld', '2025-03-14', %s, 249900, 'PLN', 'appliance')
                    returning id
                    """,
                    (user_id, f"{email.split('@')[0]} {name}", months),
                )
            ).fetchone()
            products.append(product["id"])
    return SeededUser(user_id=user_id, email=email, product_id=products[0], second_product_id=products[1])


@pytest.fixture(scope="session")
async def alice(db: Database) -> SeededUser:
    return await _create_user(db, "alice@example.test")


@pytest.fixture(scope="session")
async def bob(db: Database) -> SeededUser:
    return await _create_user(db, "bob@example.test")


@pytest.fixture
async def fresh_user(db: Database) -> SeededUser:
    """A user nobody else's test has touched, for tests that count rows."""
    return await _create_user(db, f"user-{uuid.uuid4().hex[:8]}@example.test")


@pytest.fixture(autouse=True)
def reset_mock_script():
    """Scripted model replies never leak between tests."""
    from app import mock_chat

    mock_chat.reset_script()
    yield
    mock_chat.reset_script()


@pytest.fixture(scope="session")
def store():
    """Replaces the Storage HTTP calls, and only those.

    Patched at every import site rather than at the source module, so a test can
    see exactly which boundary is faked. Everything below the download — extraction,
    chunking, embedding, the write transaction — is the real implementation.
    """
    from unittest.mock import patch

    from tests.helpers import FakeStore

    fake = FakeStore()
    patches = [
        patch("app.ingest.indexer.download_as_service", fake.download),
        patch("app.routers.documents.verify_readable", fake.verify),
        patch("app.routers.documents.delete_as_user", fake.delete),
    ]
    for item in patches:
        item.start()
    yield fake
    for item in patches:
        item.stop()


@pytest.fixture(scope="session")
async def api(settings: Settings, db: Database, store):
    """An httpx client over the ASGI app, with the real lifespan running.

    The lifespan is what creates the checkpointer tables and asserts the embedding
    column width, so skipping it would skip two of the things most worth having
    covered.
    """
    import httpx

    from app.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ai.test") as client:
            yield client


def bearer(user: "SeededUser") -> dict[str, str]:
    """A genuinely signed HS256 token for this user.

    Minted with the same secret the service verifies against, rather than patching
    the auth dependency out. It means every API test also exercises token
    verification — and that a change which breaks verification cannot pass.
    """
    import time

    import jwt

    token = jwt.encode(
        {
            "sub": str(user.user_id),
            "role": "authenticated",
            "aud": "authenticated",
            "email": user.email,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
async def corpus(settings, db, embeddings, telemetry, store, alice, bob):
    """Index the synthetic corpus: Alice gets all of it, Bob gets one document.

    Bob's document exists so that "Alice cannot see Bob's" and "Bob cannot see
    Alice's" are both real assertions rather than one of them being vacuous.
    """
    from eval.corpus import DOCUMENTS
    from tests.helpers import seed_indexed_document

    product_for = {"washer": alice.product_id, "coffee": alice.second_product_id}
    documents: dict[str, uuid.UUID] = {}

    for document in DOCUMENTS:
        documents[document.slug] = await seed_indexed_document(
            db=db,
            settings=settings,
            embeddings=embeddings,
            telemetry=telemetry,
            store=store,
            user_id=alice.user_id,
            product_id=product_for[document.product],
            title=document.title,
            text=document.text,
            kind=document.kind,
            filename=f"{document.slug}.txt",
        )

    documents["bob-only"] = await seed_indexed_document(
        db=db,
        settings=settings,
        embeddings=embeddings,
        telemetry=telemetry,
        store=store,
        user_id=bob.user_id,
        product_id=bob.product_id,
        title="Bob's private dishwasher manual",
        text=(
            "DishPro 500 service notes. Error code Z99 means the salt reservoir sensor "
            "has failed. Bob's confidential purchase reference is BOB-SECRET-4711. "
            "Replace the sensor cartridge part number DP-SENS-22."
        ),
        kind="manual",
        filename="bob-dishwasher.txt",
    )
    return documents

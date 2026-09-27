#!/usr/bin/env python
"""Drive the whole product flow over HTTP and print what happens.

    .venv/bin/python scripts/demo_walkthrough.py

This is the dry run behind `docs/AI-DEMO.md`. It performs exactly the sequence the
mobile screens perform — register a document, wait for indexing, search, ask a
question, follow a citation, answer a clarification, get a draft, restart the process,
approve, then approve again — and prints each step, so the whole flow can be checked
without a simulator.

Two things are stubbed, and only two: the Supabase Storage HTTP calls (there is no
bucket on a bare Postgres) and, by default, the providers. Everything else is the real
code path: the real migrations, real RLS, the real hybrid search SQL, the real graph
with a Postgres checkpointer.

With `PROVIDER_MODE=live` and real keys it uses the real models, which is what makes it
useful as a smoke test of a live configuration:

    PROVIDER_MODE=live OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
      .venv/bin/python scripts/demo_walkthrough.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

DEMO_DB = os.environ.get("DEMO_DATABASE_NAME", "ai_vault_demo")


def step(title: str) -> None:
    print(f"\n\033[1m── {title} ─────────────────────────────────────────\033[0m"[:100])


def note(text: str) -> None:
    print(f"   {text}")


async def main() -> int:
    from devtools.localdb import create_database, reset_schema

    dsn = create_database(DEMO_DB)
    reset_schema(dsn)
    os.environ["DATABASE_URL"] = dsn
    os.environ.setdefault("SUPABASE_JWT_SECRET", "demo-walkthrough-secret-not-a-real-one")
    os.environ.setdefault("PROVIDER_MODE", "mock")
    # The synthetic corpus is a handful of short text files. At the production chunk
    # size each one is a chunk or two, so a citation would read "characters 1–1353" —
    # technically true and useless to look at. Smaller chunks here make the citations
    # specific enough to be worth following, which is the point of the demo. Real
    # manuals are PDFs with page numbers and need no such adjustment.
    os.environ.setdefault("CHUNK_TARGET_TOKENS", "110")
    os.environ.setdefault("CHUNK_OVERLAP_TOKENS", "24")

    from unittest.mock import patch

    import httpx
    import jwt

    from app.config import get_settings
    from app.db import fetch_all, fetch_one
    from app.ingest import indexer as indexer_module
    from app.ingest.worker import drain
    from app.providers import build_embeddings
    from app.storage import StoredObject
    from app.telemetry import Telemetry
    from eval.corpus import DOCUMENTS

    get_settings.cache_clear()
    settings = get_settings()

    objects: dict[str, bytes] = {}

    async def fake_download(_settings, path, *, bucket="", max_bytes=None):
        return StoredObject(data=objects[path], content_type="text/plain", byte_size=len(objects[path]))

    async def fake_verify(_settings, path, _token, *, bucket=""):
        return path in objects

    async def fake_delete(_settings, path, _token, *, bucket=""):
        objects.pop(path, None)

    patches = [
        patch.object(indexer_module, "download_as_service", fake_download),
        patch("app.routers.documents.verify_readable", fake_verify),
        patch("app.routers.documents.delete_as_user", fake_delete),
    ]
    for item in patches:
        item.start()

    from app.main import app

    async with app.router.lifespan_context(app):
        services = app.state.services
        db = services.db

        step("A user with one product")
        async with db.service_scope() as conn:
            user = await fetch_one(
                conn, "insert into auth.users (email) values ('demo@example.test') returning id"
            )
            # The handle_new_user() signup trigger has already created the profile;
            # this is only here so the script also works against a database where
            # that trigger is absent.
            await conn.execute(
                "insert into public.profiles (id, email) values (%s, 'demo@example.test') "
                "on conflict (id) do nothing",
                (user["id"],),
            )
            product = await fetch_one(
                conn,
                """
                insert into public.products
                  (user_id, name, merchant, purchase_date, warranty_months, price_cents, currency, category)
                values (%s, 'AquaWash 8000 washing machine', 'TechWorld', '2025-03-14', 24, 249900, 'PLN', 'appliance')
                returning id, name, warranty_expires_at
                """,
                (user["id"],),
            )
        note(f"user {user['id']}")
        note(f"product {product['name']} — warranty runs to {product['warranty_expires_at']}")

        token = jwt.encode(
            {
                "sub": str(user["id"]),
                "role": "authenticated",
                "aud": "authenticated",
                "exp": int(time.time()) + 3600,
            },
            settings.supabase_jwt_secret,
            algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://demo", headers=headers) as api:
            step("Attaching the documents (what the Documents screen does)")
            for document in DOCUMENTS:
                if document.product != "washer":
                    continue
                path = f"{user['id']}/{product['id']}/{document.slug}.txt"
                objects[path] = document.text.encode("utf-8")
                created = await api.post(
                    f"/v1/products/{product['id']}/documents",
                    json={
                        "storage_path": path,
                        "title": document.title,
                        "kind": document.kind,
                        "mime_type": "text/plain",
                        "byte_size": len(objects[path]),
                    },
                )
                created.raise_for_status()
                note(f"{document.title}: {created.json()['status']} (job {created.json()['job_state']})")

            step("The indexing worker runs")
            started = time.perf_counter()
            processed = await drain(db, settings, Telemetry(db), max_jobs=20)
            note(f"{processed} job(s) in {time.perf_counter() - started:.1f}s")
            listed = (await api.get(f"/v1/products/{product['id']}/documents")).json()
            for row in listed:
                note(
                    f"{row['title']}: {row['status']} · {row['chunk_count']} passages "
                    f"· {row['extraction_method']}"
                )

            step("Search, worded differently from the document")
            found = (
                await api.post(
                    "/v1/search",
                    json={"query": "jak sie dobrac do filtra zeby go przeplukac", "top_k": 3},
                )
            ).json()
            note(f"mode={found['mode']} hits={len(found['hits'])}")
            for hit in found["hits"]:
                note(f"  {hit['document_title']} · {hit['locator']} :: {hit['snippet'][:70]}…")

            step("A question, answered with a citation")
            thread_id = (
                await api.post("/v1/threads", json={"product_id": str(product["id"])})
            ).json()["thread_id"]
            turn = (
                await api.post(
                    f"/v1/threads/{thread_id}/messages", json={"content": "Co oznacza kod E17?"}
                )
            ).json()
            note(f"outcome={turn['outcome']}")
            note(f"answer: {turn['message']['content'][:160]}")
            for citation in turn["message"]["citations"]:
                note(f"  cites {citation['document_title']} · {citation['locator']}")

            if turn["message"]["citations"]:
                citation = turn["message"]["citations"][0]
                step("Following the citation (what tapping a source does)")
                chunks = (
                    await api.get(f"/v1/documents/{citation['document_id']}/chunks")
                ).json()
                match = next(c for c in chunks if c["chunk_id"] == citation["chunk_id"])
                note(f"{citation['locator']}: {match['content'][:140]}…")

            step("A question the documents cannot answer")
            unanswerable = (
                await api.post(
                    f"/v1/threads/{thread_id}/messages",
                    json={"content": "Ile kosztuje wymiana lozyska w tej pralce?"},
                )
            ).json()
            note(f"outcome={unanswerable['outcome']}")
            note(f"answer: {unanswerable['message']['content'][:160]}")

            step("A clarifying question, and the resume that answers it")
            from app import mock_chat

            if settings.is_mock:
                mock_chat.push_response(
                    {
                        "intent": "question",
                        "needs_clarification": True,
                        "clarifying_question": "Jaki kod bledu pokazuje pralka?",
                        "reason": "no code given",
                    }
                )
            asked = (
                await api.post(
                    f"/v1/threads/{thread_id}/messages", json={"content": "Cos nie dziala."}
                )
            ).json()
            note(f"outcome={asked['outcome']}")
            if asked["awaiting"]:
                note(f"asks: {asked['awaiting']['question']}")
                answered = (
                    await api.post(
                        f"/v1/threads/{thread_id}/resume", json={"answer": "Pokazuje E17."}
                    )
                ).json()
                note(f"after resume: outcome={answered['outcome']}")
                note(f"answer: {answered['message']['content'][:140]}")

            step("A service-request draft, waiting for a person")
            if settings.is_mock:
                from tests.test_graph import DRAFT_SCRIPT

                mock_chat.push_responses(DRAFT_SCRIPT)
            offered = (
                await api.post(
                    f"/v1/threads/{thread_id}/messages",
                    json={
                        "content": "Przygotuj zgloszenie serwisowe. Pralka przerywa program i pokazuje E17."
                    },
                )
            ).json()
            note(f"outcome={offered['outcome']}")
            draft = (offered.get("awaiting") or {}).get("draft")
            if not draft:
                note("no draft was proposed; stopping here")
                return 1
            note(f"subject: {draft['subject']}")
            note(f"body:\n      " + draft["body"].replace("\n", "\n      ")[:400])
            if draft["missing_information"]:
                note(f"gaps it refused to invent: {draft['missing_information']}")

        step("Restart: everything in memory is thrown away")
        # A brand-new lifespan means new pools, a new checkpointer and a freshly
        # compiled graph. The only thing carried across is the thread id.
        for item in patches:
            item.stop()

    from app.config import get_settings as fresh_settings

    fresh_settings.cache_clear()
    for item in patches:
        item.start()

    from importlib import reload

    import app.main as main_module

    reload(main_module)
    restarted = main_module.app

    async with restarted.router.lifespan_context(restarted):
        transport = httpx.ASGITransport(app=restarted)
        async with httpx.AsyncClient(transport=transport, base_url="http://demo", headers=headers) as api:
            step("The pending draft is still there, read from Postgres")
            state = (await api.get(f"/v1/threads/{thread_id}")).json()
            awaiting = state["awaiting"]
            note(f"awaiting: {awaiting and awaiting['kind']}")
            note(f"transcript: {len(state['messages'])} messages restored from the backend")
            if not awaiting or awaiting["kind"] != "claim_draft":
                note("the draft did not survive the restart — that is a failure")
                return 1

            pending = awaiting["draft"]
            edited = pending["body"] + "\n\nKod powtarza sie po wyczyszczeniu filtra."

            step("Approving the edited version, exactly once")
            approved = (
                await api.post(
                    f"/v1/threads/{thread_id}/resume",
                    json={"decision": "approve", "subject": pending["subject"], "body": edited},
                )
            ).json()
            note(f"outcome={approved['outcome']} applied={approved['draft_result']['applied']}")

            again = (
                await api.post(
                    f"/v1/threads/{thread_id}/resume",
                    json={"decision": "approve", "subject": "Hijacked", "body": "Hijacked"},
                )
            ).json()
            note(f"second attempt: applied={again['draft_result']['applied']} reason={again['draft_result'].get('reason')}")

            final = (await api.get(f"/v1/threads/{thread_id}")).json()["latest_draft"]
            note(f"stored status={final['status']} version={final['version']}")
            note(f"approved text ends with: …{final['approved_body'][-60:].strip()}")

            services = restarted.state.services
            async with services.db.service_scope() as conn:
                rows = await fetch_all(
                    conn,
                    "select status::text as status from public.claim_drafts where thread_id = %s",
                    (uuid.UUID(thread_id),),
                )
            approved_count = sum(1 for row in rows if row["status"] == "approved")
            note(f"approved rows in the database: {approved_count}")
            if approved_count != 1 or "Hijacked" in (final["approved_body"] or ""):
                note("exactly-once approval was violated — that is a failure")
                return 1

            step("Developer view of the same conversation")
            trace = (await api.get(f"/v1/debug/threads/{thread_id}/trace")).json()
            note(f"next nodes: {trace['next_nodes']}")
            note(f"intent={trace['intent']} tool_steps={trace['tool_steps']} outcome={trace['outcome']}")
            note(f"tools called: {[c for m in trace['messages'] for c in m['tool_calls']]}")
            note(f"checkpoints recorded: {len(trace['checkpoint_history'])}")

    for item in patches:
        item.stop()

    print("\n\033[1mWalkthrough completed.\033[0m")
    if os.environ.get("PROVIDER_MODE", "mock") == "mock":
        print(
            "Ran with PROVIDER_MODE=mock: the flow, the access scoping and the exactly-once\n"
            "approval are real. The wording of answers is not — that needs real providers."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

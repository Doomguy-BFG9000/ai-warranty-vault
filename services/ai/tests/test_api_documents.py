"""HTTP behaviour for documents, including the cases that must *not* work.

Every request carries a genuinely signed token, so these also cover token
verification rather than stubbing the auth dependency out.
"""

from __future__ import annotations

import uuid

from tests.conftest import bearer
from tests.helpers import object_path


async def _upload(store, user, product_id, text: str, name: str = "manual.txt") -> str:
    path = object_path(user.user_id, product_id, name)
    store.put(path, text.encode("utf-8"), "text/plain")
    return path


async def test_register_list_and_index_a_document(api, store, settings, db, telemetry, fresh_user):
    from app.ingest.worker import drain

    path = await _upload(store, fresh_user, fresh_user.product_id, "Kod E17 oznacza problem z odprowadzaniem wody. " * 6)

    created = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents",
        json={
            "storage_path": path,
            "title": "Instrukcja obsługi",
            "kind": "manual",
            "mime_type": "text/plain",
            "byte_size": 400,
        },
        headers=bearer(fresh_user),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    # The client is told a job is live, which is how the UI distinguishes "failed"
    # from "failed, retrying".
    assert body["job_state"] == "queued"

    await drain(db, settings, telemetry, max_jobs=5)

    listed = await api.get(
        f"/v1/products/{fresh_user.product_id}/documents", headers=bearer(fresh_user)
    )
    assert listed.status_code == 200
    document = listed.json()[0]
    assert document["status"] == "ready"
    assert document["chunk_count"] > 0
    assert document["extraction_method"] == "plain_text"
    assert document["stale_index"] is False
    assert document["job_state"] is None

    chunks = await api.get(f"/v1/documents/{document['id']}/chunks", headers=bearer(fresh_user))
    assert chunks.status_code == 200
    assert chunks.json()
    # A .txt document cites character ranges, not invented pages.
    assert all(chunk["page_number"] is None for chunk in chunks.json())
    assert all(chunk["locator"].startswith("characters ") for chunk in chunks.json())


async def test_registering_against_another_users_product_is_a_404(api, store, alice, bob):
    path = await _upload(store, bob, alice.product_id, "text", name="sneaky.txt")
    response = await api.post(
        f"/v1/products/{alice.product_id}/documents",
        json={
            "storage_path": path,
            "title": "not mine",
            "kind": "manual",
            "mime_type": "text/plain",
            "byte_size": 10,
        },
        headers=bearer(bob),
    )
    # 404, not 403: a 403 would confirm that Alice's product id exists.
    assert response.status_code == 404


async def test_a_path_outside_the_callers_own_prefix_is_rejected(api, store, alice, bob):
    path = await _upload(store, alice, alice.product_id, "text", name="theirs.txt")
    response = await api.post(
        f"/v1/products/{bob.product_id}/documents",
        json={
            "storage_path": path,
            "title": "borrowed",
            "kind": "manual",
            "mime_type": "text/plain",
            "byte_size": 10,
        },
        headers=bearer(bob),
    )
    assert response.status_code == 400


async def test_an_unreadable_upload_is_not_registered(api, store, fresh_user):
    # Never put in the store, so the caller's token cannot read it.
    response = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents",
        json={
            "storage_path": object_path(fresh_user.user_id, fresh_user.product_id, "ghost.txt"),
            "title": "ghost",
            "kind": "manual",
            "mime_type": "text/plain",
            "byte_size": 10,
        },
        headers=bearer(fresh_user),
    )
    assert response.status_code == 400


async def test_an_unsupported_file_type_is_refused(api, store, fresh_user):
    path = await _upload(store, fresh_user, fresh_user.product_id, "x", name="thing.docx")
    response = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents",
        json={
            "storage_path": path,
            "title": "spreadsheet",
            "kind": "other",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "byte_size": 10,
        },
        headers=bearer(fresh_user),
    )
    assert response.status_code == 422


async def test_an_oversized_file_is_refused_before_anything_is_stored(api, store, settings, fresh_user):
    path = await _upload(store, fresh_user, fresh_user.product_id, "x", name="huge.txt")
    response = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents",
        json={
            "storage_path": path,
            "title": "huge",
            "kind": "other",
            "mime_type": "text/plain",
            "byte_size": settings.max_document_bytes + 1,
        },
        headers=bearer(fresh_user),
    )
    assert response.status_code == 400


async def test_registering_the_same_object_twice_is_a_conflict(api, store, fresh_user):
    path = await _upload(store, fresh_user, fresh_user.product_id, "some text here", name="dup.txt")
    payload = {
        "storage_path": path,
        "title": "duplicate",
        "kind": "manual",
        "mime_type": "text/plain",
        "byte_size": 14,
    }
    first = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents", json=payload, headers=bearer(fresh_user)
    )
    assert first.status_code == 201
    second = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents", json=payload, headers=bearer(fresh_user)
    )
    assert second.status_code == 409


async def test_reading_another_users_document_is_a_404(api, corpus, bob):
    document_id = corpus["aquawash-8000-manual-pl"]
    assert (await api.get(f"/v1/documents/{document_id}", headers=bearer(bob))).status_code == 404
    assert (
        await api.get(f"/v1/documents/{document_id}/chunks", headers=bearer(bob))
    ).status_code == 404
    assert (
        await api.post(f"/v1/documents/{document_id}/reindex", headers=bearer(bob))
    ).status_code == 404
    assert (await api.delete(f"/v1/documents/{document_id}", headers=bearer(bob))).status_code == 404


async def test_an_invented_document_id_is_also_a_404(api, alice):
    response = await api.get(f"/v1/documents/{uuid.uuid4()}", headers=bearer(alice))
    assert response.status_code == 404


async def test_retrying_twice_in_a_row_is_a_conflict(api, store, db, settings, telemetry, fresh_user):
    from app.ingest.worker import drain

    path = await _upload(store, fresh_user, fresh_user.product_id, "Treść dokumentu do zaindeksowania. " * 8, name="retry.txt")
    created = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents",
        json={
            "storage_path": path,
            "title": "retryable",
            "kind": "manual",
            "mime_type": "text/plain",
            "byte_size": 300,
        },
        headers=bearer(fresh_user),
    )
    document_id = created.json()["id"]
    await drain(db, settings, telemetry, max_jobs=5)

    assert (await api.post(f"/v1/documents/{document_id}/reindex", headers=bearer(fresh_user))).status_code == 200
    # A second tap while the first job is still live.
    assert (await api.post(f"/v1/documents/{document_id}/reindex", headers=bearer(fresh_user))).status_code == 409


async def test_deleting_a_document_removes_its_chunks_and_its_object(
    api, store, db, settings, telemetry, fresh_user
):
    from app.ingest.worker import drain
    from tests.helpers import chunk_count

    path = await _upload(store, fresh_user, fresh_user.product_id, "Dokument do usunięcia. " * 10, name="gone.txt")
    created = await api.post(
        f"/v1/products/{fresh_user.product_id}/documents",
        json={
            "storage_path": path,
            "title": "temporary",
            "kind": "other",
            "mime_type": "text/plain",
            "byte_size": 200,
        },
        headers=bearer(fresh_user),
    )
    document_id = created.json()["id"]
    await drain(db, settings, telemetry, max_jobs=5)
    assert await chunk_count(db, uuid.UUID(document_id)) > 0

    deleted = await api.delete(f"/v1/documents/{document_id}", headers=bearer(fresh_user))
    assert deleted.status_code == 204
    assert await chunk_count(db, uuid.UUID(document_id)) == 0
    assert path in store.deleted


async def test_search_is_scoped_to_the_caller(api, corpus, alice, bob):
    alice_results = await api.post(
        "/v1/search", json={"query": "E17 filtr pompy"}, headers=bearer(alice)
    )
    assert alice_results.status_code == 200
    assert alice_results.json()["hits"]

    bob_results = await api.post(
        "/v1/search", json={"query": "E17 problem z odprowadzaniem wody"}, headers=bearer(bob)
    )
    assert bob_results.status_code == 200
    assert all("odprowadzaniem" not in hit["snippet"] for hit in bob_results.json()["hits"])


async def test_search_reports_when_there_is_nothing_indexed_to_search(api, fresh_user):
    """"No results" and "no documents" are different problems with different fixes."""
    response = await api.post(
        "/v1/search",
        json={"query": "anything at all", "product_id": str(fresh_user.product_id)},
        headers=bearer(fresh_user),
    )
    assert response.status_code == 200
    assert response.json()["no_documents"] is True
    assert response.json()["hits"] == []


async def test_search_in_another_users_product_returns_nothing(api, corpus, alice, bob):
    response = await api.post(
        "/v1/search",
        json={"query": "filtr pompy", "product_id": str(alice.product_id)},
        headers=bearer(bob),
    )
    assert response.status_code == 200
    assert response.json()["hits"] == []


async def test_requests_without_a_valid_token_are_rejected(api, alice):
    assert (await api.post("/v1/search", json={"query": "x"})).status_code == 401
    assert (
        await api.post("/v1/search", json={"query": "x"}, headers={"Authorization": "Bearer nonsense"})
    ).status_code == 401
    assert (
        await api.post("/v1/search", json={"query": "x"}, headers={"Authorization": "Basic abc"})
    ).status_code == 401


async def test_the_meta_endpoint_exposes_configuration_and_no_secrets(api):
    response = await api.get("/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["index_version"]
    assert body["retrieval"]["mode"] in ("vector", "lexical", "hybrid")
    serialised = response.text.lower()
    for forbidden in ("secret", "api_key", "apikey", "service_role", "jwt"):
        assert forbidden not in serialised


async def test_every_response_carries_an_operation_id(api, alice):
    response = await api.post("/v1/search", json={"query": "filtr"}, headers=bearer(alice))
    assert response.headers.get("X-Operation-Id")

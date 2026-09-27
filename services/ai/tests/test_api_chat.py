"""HTTP behaviour for conversations: transcripts, clarification, drafts, ownership."""

from __future__ import annotations

import uuid

from app import mock_chat
from app.db import fetch_all
from tests.conftest import bearer
from tests.test_graph import DRAFT_SCRIPT


async def _thread(api, user, product_id=None) -> str:
    response = await api.post(
        "/v1/threads",
        json={"product_id": str(product_id) if product_id else None},
        headers=bearer(user),
    )
    assert response.status_code == 201, response.text
    return response.json()["thread_id"]


async def test_a_question_returns_an_answer_with_openable_citations(api, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)

    turn = await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Co oznacza kod E17?"},
        headers=bearer(alice),
    )
    assert turn.status_code == 200, turn.text
    body = turn.json()
    assert body["outcome"] == "answered"
    assert body["message"]["citations"]

    citation = body["message"]["citations"][0]
    assert citation["document_id"] and citation["chunk_id"]

    # "Open the source" has to actually work: the cited document and chunk are both
    # readable by this caller.
    document = await api.get(f"/v1/documents/{citation['document_id']}", headers=bearer(alice))
    assert document.status_code == 200
    chunks = await api.get(
        f"/v1/documents/{citation['document_id']}/chunks", headers=bearer(alice)
    )
    assert citation["chunk_id"] in {chunk["chunk_id"] for chunk in chunks.json()}


async def test_the_transcript_is_restored_from_the_backend(api, corpus, alice):
    """What a cold app start sees after being killed mid-conversation."""
    thread_id = await _thread(api, alice, alice.product_id)
    await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Jak wyczyścić filtr pompy?"},
        headers=bearer(alice),
    )

    state = await api.get(f"/v1/threads/{thread_id}", headers=bearer(alice))
    assert state.status_code == 200
    body = state.json()
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["user", "assistant"]
    assert body["awaiting"] is None
    assert body["messages"][1]["citations"]


async def test_a_clarification_is_asked_persisted_and_resumed(api, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)
    mock_chat.push_response(
        {
            "intent": "question",
            "needs_clarification": True,
            "clarifying_question": "Jaki kod błędu pokazuje pralka?",
            "reason": "no code",
        }
    )

    first = await api.post(
        f"/v1/threads/{thread_id}/messages", json={"content": "Coś nie działa."}, headers=bearer(alice)
    )
    assert first.status_code == 200
    assert first.json()["outcome"] == "clarification_needed"
    awaiting = first.json()["awaiting"]
    assert awaiting["kind"] == "clarification"
    assert awaiting["question"] == "Jaki kod błędu pokazuje pralka?"

    # A reopened app finds the pending question waiting for it.
    reopened = await api.get(f"/v1/threads/{thread_id}", headers=bearer(alice))
    assert reopened.json()["awaiting"]["kind"] == "clarification"

    # Asking something new while a question is pending is refused, so the interrupt
    # cannot be stranded.
    blocked = await api.post(
        f"/v1/threads/{thread_id}/messages", json={"content": "Inne pytanie"}, headers=bearer(alice)
    )
    assert blocked.status_code == 400

    resumed = await api.post(
        f"/v1/threads/{thread_id}/resume", json={"answer": "Pokazuje E17."}, headers=bearer(alice)
    )
    assert resumed.status_code == 200
    assert resumed.json()["outcome"] in ("answered", "no_answer")

    final = await api.get(f"/v1/threads/{thread_id}", headers=bearer(alice))
    assert final.json()["awaiting"] is None
    assert [message["role"] for message in final.json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


async def test_a_draft_is_offered_edited_and_approved_exactly_once(api, db, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)
    mock_chat.push_responses(DRAFT_SCRIPT)

    offered = await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Przygotuj zgłoszenie serwisowe, pralka pokazuje E17."},
        headers=bearer(alice),
    )
    assert offered.status_code == 200, offered.text
    assert offered.json()["outcome"] == "draft_ready"
    draft = offered.json()["awaiting"]["draft"]
    assert draft["status"] == "pending"
    assert "E17" in draft["body"]

    edited_body = draft["body"] + "\n\nKod powtarza się po wyczyszczeniu filtra."
    approved = await api.post(
        f"/v1/threads/{thread_id}/resume",
        json={"decision": "approve", "subject": draft["subject"], "body": edited_body},
        headers=bearer(alice),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["outcome"] == "draft_approved"
    assert approved.json()["draft_result"]["applied"] is True

    state = await api.get(f"/v1/threads/{thread_id}", headers=bearer(alice))
    latest = state.json()["latest_draft"]
    assert latest["status"] == "approved"
    assert latest["approved_body"] == edited_body
    assert state.json()["awaiting"] is None

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select status::text as status from public.claim_drafts where thread_id = %s",
            (uuid.UUID(thread_id),),
        )
    assert [row["status"] for row in rows].count("approved") == 1


async def test_approving_an_already_decided_draft_is_idempotent(api, db, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)
    mock_chat.push_responses(DRAFT_SCRIPT)
    offered = await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Przygotuj zgłoszenie serwisowe."},
        headers=bearer(alice),
    )
    draft = offered.json()["awaiting"]["draft"]

    first = await api.post(
        f"/v1/threads/{thread_id}/resume",
        json={"decision": "approve", "subject": draft["subject"], "body": draft["body"]},
        headers=bearer(alice),
    )
    assert first.json()["draft_result"]["applied"] is True

    # A retried request, or a double-tapped Approve button.
    for _ in range(3):
        again = await api.post(
            f"/v1/threads/{thread_id}/resume",
            json={"decision": "approve", "subject": "Hijacked", "body": "Hijacked"},
            headers=bearer(alice),
        )
        assert again.status_code == 200
        assert again.json()["draft_result"]["applied"] is False
        assert again.json()["draft_result"]["reason"] == "already_approved"

    state = await api.get(f"/v1/threads/{thread_id}", headers=bearer(alice))
    latest = state.json()["latest_draft"]
    assert latest["approved_body"] == draft["body"]
    assert "Hijacked" not in latest["approved_body"]

    async with db.service_scope() as conn:
        rows = await fetch_all(
            conn,
            "select status::text as status from public.claim_drafts where thread_id = %s",
            (uuid.UUID(thread_id),),
        )
    assert [row["status"] for row in rows].count("approved") == 1


async def test_resuming_with_the_wrong_kind_of_payload_is_refused(api, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)
    mock_chat.push_response(
        {
            "intent": "question",
            "needs_clarification": True,
            "clarifying_question": "Który model?",
            "reason": "ambiguous",
        }
    )
    await api.post(
        f"/v1/threads/{thread_id}/messages", json={"content": "Nie działa."}, headers=bearer(alice)
    )
    # Waiting for a sentence, not a draft decision.
    response = await api.post(
        f"/v1/threads/{thread_id}/resume", json={"decision": "approve"}, headers=bearer(alice)
    )
    assert response.status_code == 400


async def test_another_user_cannot_read_resume_or_post_to_a_thread(api, corpus, alice, bob):
    """Ownership is re-checked on every call, resumes included.

    The graph's checkpoint tables have no RLS at all, so this is the check that keeps
    a conversation private — it is done against public.ai_threads before LangGraph is
    given the thread id.
    """
    thread_id = await _thread(api, alice, alice.product_id)
    mock_chat.push_responses(DRAFT_SCRIPT)
    await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Przygotuj zgłoszenie serwisowe."},
        headers=bearer(alice),
    )

    assert (await api.get(f"/v1/threads/{thread_id}", headers=bearer(bob))).status_code == 404
    assert (
        await api.post(
            f"/v1/threads/{thread_id}/messages", json={"content": "hello"}, headers=bearer(bob)
        )
    ).status_code == 404
    assert (
        await api.post(
            f"/v1/threads/{thread_id}/resume",
            json={"decision": "approve", "subject": "mine", "body": "mine"},
            headers=bearer(bob),
        )
    ).status_code == 404
    assert (
        await api.get(f"/v1/debug/threads/{thread_id}/trace", headers=bearer(bob))
    ).status_code == 404

    # And Alice's draft is untouched.
    state = await api.get(f"/v1/threads/{thread_id}", headers=bearer(alice))
    assert state.json()["latest_draft"]["status"] == "pending"


async def test_a_thread_cannot_be_opened_on_another_users_product(api, alice, bob):
    response = await api.post(
        "/v1/threads", json={"product_id": str(alice.product_id)}, headers=bearer(bob)
    )
    assert response.status_code == 404


async def test_the_user_facing_payload_leaks_no_graph_internals(api, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)
    turn = await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Co oznacza kod E17?"},
        headers=bearer(alice),
    )
    serialised = turn.text
    for internal in ("await_approval", "draft_claim", "compose", "checkpoint", "__interrupt__", "tool_calls"):
        assert internal not in serialised


async def test_the_developer_trace_shows_the_run_for_its_owner(api, corpus, alice):
    thread_id = await _thread(api, alice, alice.product_id)
    await api.post(
        f"/v1/threads/{thread_id}/messages",
        json={"content": "Co oznacza kod E17?"},
        headers=bearer(alice),
    )

    trace = await api.get(f"/v1/debug/threads/{thread_id}/trace", headers=bearer(alice))
    assert trace.status_code == 200
    body = trace.json()
    # This is the surface where node names and scores are fine, because it is for
    # whoever is debugging their own conversation.
    assert body["intent"] == "question"
    assert body["tool_steps"] >= 1
    assert any(message["tool_calls"] for message in body["messages"])
    assert body["sources"] and body["sources"][0]["score"] is not None
    assert body["checkpoint_history"]


async def test_retrieval_comparison_is_available_to_a_developer(api, corpus, alice):
    response = await api.post(
        "/v1/debug/retrieval/compare",
        params={"query": "E23 zamek drzwi", "product_id": str(alice.product_id)},
        headers=bearer(alice),
    )
    assert response.status_code == 200
    modes = response.json()["modes"]
    assert set(modes) == {"vector", "lexical", "hybrid"}
    assert any(hit for hit in modes["lexical"])


async def test_the_operations_log_endpoint_returns_only_the_callers_own_rows(api, corpus, alice, bob):
    await api.post("/v1/search", json={"query": "filtr pompy"}, headers=bearer(alice))
    response = await api.get("/v1/debug/operations", headers=bearer(bob))
    assert response.status_code == 200
    # Bob has his own rows at most; nothing of Alice's, and no content anywhere.
    serialised = response.text
    assert "filtr pompy" not in serialised

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.fakes import register_user, upload_pdf


async def test_summarize_document_queues_and_caches_by_style(
    client: AsyncClient,
    processed_document,
    chat_provider,
    dispatcher,
    run_summarization,
) -> None:
    await register_user(client)
    document = await processed_document(client)
    chat_provider.response = "This agreement requires payment within thirty days."

    queued = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "short"},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "pending"
    assert queued.json()["summary"] is None
    assert queued.json()["style"] == "short"
    assert len(dispatcher.summary_jobs) == 1
    job = dispatcher.summary_jobs[0]
    assert job["document_id"] == uuid.UUID(document["id"])
    assert chat_provider.calls == []

    pending = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    assert await run_summarization(uuid.UUID(document["id"]), job["style"], job["attempt_id"])
    completed = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert completed.json()["status"] == "completed"
    assert completed.json()["summary"] == chat_provider.response
    assert len(chat_provider.calls) == 1
    assert "untrusted" in chat_provider.calls[0]["system_prompt"].lower()

    cached = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "short", "force": False},
    )
    assert cached.status_code == 200
    assert cached.json()["summary"] == chat_provider.response
    assert cached.json()["status"] == "completed"
    assert len(chat_provider.calls) == 1
    assert len(dispatcher.summary_jobs) == 1

    detailed = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "detailed"},
    )
    assert detailed.status_code == 202
    detailed_job = dispatcher.summary_jobs[-1]
    assert detailed_job["style"] == "detailed"
    await run_summarization(
        uuid.UUID(document["id"]), detailed_job["style"], detailed_job["attempt_id"]
    )
    assert len(chat_provider.calls) == 2

    chat_provider.response = "A regenerated summary."
    forced = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "detailed", "force": True},
    )
    assert forced.status_code == 202
    forced_job = dispatcher.summary_jobs[-1]
    await run_summarization(
        uuid.UUID(document["id"]), forced_job["style"], forced_job["attempt_id"]
    )
    result = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert result.json()["summary"] == "A regenerated summary."
    assert len(chat_provider.calls) == 3

    detail = await client.get(f"/api/v1/documents/{document['id']}")
    assert detail.json()["summary"] == "A regenerated summary."
    assert detail.json()["summary_style"] == "detailed"
    assert detail.json()["summary_status"] == "completed"
    assert detail.json()["summarized_at"] is not None


async def test_summarize_uses_map_reduce_for_long_documents(
    client: AsyncClient,
    processed_document,
    chat_provider,
    dispatcher,
    run_summarization,
    test_settings,
    monkeypatch,
) -> None:
    await register_user(client)
    document = await processed_document(client)
    monkeypatch.setattr(test_settings, "summary_map_chunk_chars", 20)
    monkeypatch.setattr(test_settings, "summary_reduce_chunk_chars", 40)
    chat_provider.response = "Merged factual summary."

    response = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "short"},
    )
    assert response.status_code == 202, response.text
    job = dispatcher.summary_jobs[-1]
    await run_summarization(uuid.UUID(document["id"]), job["style"], job["attempt_id"])
    result = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert result.json()["status"] == "completed"
    assert result.json()["summary"] == "Merged factual summary."
    assert len(chat_provider.calls) >= 3
    assert any("PAGE RANGE" in call["user_prompt"] for call in chat_provider.calls)
    assert any("PARTIAL SUMMARIES" in call["user_prompt"] for call in chat_provider.calls)


async def test_summarize_requires_completed_owned_document(
    client_factory, chat_provider, dispatcher
) -> None:
    alice = await client_factory()
    bob = await client_factory()
    await register_user(alice, email="alice@example.com")
    await register_user(bob, email="bob@example.com")
    pending = (await upload_pdf(alice)).json()

    not_ready = await alice.post(
        f"/api/v1/documents/{pending['id']}/summarize", json={"style": "short"}
    )
    assert not_ready.status_code == 409
    assert "finish processing" in not_ready.json()["error"]["message"]

    foreign = await bob.post(
        f"/api/v1/documents/{pending['id']}/summarize", json={"style": "short"}
    )
    assert foreign.status_code == 404
    assert (await bob.get(f"/api/v1/documents/{pending['id']}/summary")).status_code == 404
    assert chat_provider.calls == []
    assert dispatcher.summary_jobs == []


async def test_conversation_crud(client: AsyncClient) -> None:
    await register_user(client)
    created = await client.post("/api/v1/conversations", json={"title": "  Payment   questions  "})
    assert created.status_code == 201
    conversation = created.json()
    assert conversation["title"] == "Payment questions"
    assert conversation["message_count"] == 0

    default_title = await client.post("/api/v1/conversations", json={})
    assert default_title.status_code == 201
    assert default_title.json()["title"] == "New conversation"

    blank = await client.post("/api/v1/conversations", json={"title": "   "})
    assert blank.status_code == 422

    listing = await client.get("/api/v1/conversations")
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    assert len(listing.json()["items"]) == 2
    assert all(item["message_count"] == 0 for item in listing.json()["items"])

    detail = await client.get(f"/api/v1/conversations/{conversation['id']}")
    assert detail.status_code == 200
    assert detail.json()["messages"] == []

    deleted = await client.delete(f"/api/v1/conversations/{conversation['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}")).status_code == 404
    assert (await client.delete(f"/api/v1/conversations/{conversation['id']}")).status_code == 404

    paginated = await client.get("/api/v1/conversations", params={"page": 1, "page_size": 1})
    assert paginated.json()["total"] == 1
    assert paginated.json()["pages"] == 1


async def test_conversation_ownership_and_message_counts(
    client_factory, processed_document, chat_provider
) -> None:
    alice = await client_factory()
    bob = await client_factory()
    await register_user(alice, email="alice@example.com")
    await register_user(bob, email="bob@example.com")
    await processed_document(alice)

    chat = await alice.post("/api/v1/chat", json={"question": "What are the payment terms?"})
    assert chat.status_code == 200
    conversation_id = chat.json()["conversation_id"]

    listing = await alice.get("/api/v1/conversations")
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["message_count"] == 2
    assert listing.json()["items"][0]["id"] == conversation_id

    assert (await bob.get("/api/v1/conversations")).json()["total"] == 0
    assert (await bob.get(f"/api/v1/conversations/{conversation_id}")).status_code == 404
    assert (await bob.delete(f"/api/v1/conversations/{conversation_id}")).status_code == 404

    second = await alice.post(
        "/api/v1/chat",
        json={
            "question": "Can you clarify the payment window?",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id
    follow_up_prompt = chat_provider.calls[-1]["user_prompt"]
    assert "CONVERSATION HISTORY" in follow_up_prompt
    assert "What are the payment terms?" in follow_up_prompt
    assert chat_provider.response in follow_up_prompt
    detail = await alice.get(f"/api/v1/conversations/{conversation_id}")
    assert len(detail.json()["messages"]) == 4

    deleted = await alice.delete(f"/api/v1/conversations/{conversation_id}")
    assert deleted.status_code == 204
    assert (await alice.get(f"/api/v1/conversations/{conversation_id}")).status_code == 404


async def test_conversation_missing_id_returns_404(client: AsyncClient) -> None:
    await register_user(client)
    assert (await client.get(f"/api/v1/conversations/{uuid.uuid4()}")).status_code == 404
    assert (await client.delete(f"/api/v1/conversations/{uuid.uuid4()}")).status_code == 404

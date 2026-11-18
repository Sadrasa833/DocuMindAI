from __future__ import annotations

from httpx import AsyncClient

from tests.fakes import DEFAULT_PAGES, register_user


async def test_deleted_document_citations_are_preserved_and_marked_unavailable(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    document = await processed_document(client, DEFAULT_PAGES)
    chat_provider.response = "Payment is due within 30 days [S1]."

    chat = await client.post(
        "/api/v1/chat",
        json={
            "question": "What are the payment terms?",
            "document_ids": [document["id"]],
        },
    )
    assert chat.status_code == 200, chat.text
    chat_body = chat.json()
    original = chat_body["citations"][0]
    assert original["available"] is True

    deleted = await client.delete(f"/api/v1/documents/{document['id']}")
    assert deleted.status_code == 204

    detail = await client.get(f"/api/v1/conversations/{chat_body['conversation_id']}")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    historical = messages[-1]["citations"][0]

    assert historical["available"] is False
    assert historical["document_id"] == original["document_id"]
    assert historical["document_name"] == original["document_name"]
    assert historical["page_number"] == original["page_number"]
    assert historical["chunk_id"] == original["chunk_id"]
    assert messages[-1]["content"] == chat_body["answer"]


async def test_citation_availability_is_resolved_per_document_at_read_time(
    client: AsyncClient, processed_document, chat_provider, test_settings, monkeypatch
) -> None:
    await register_user(client)
    monkeypatch.setattr(test_settings, "rag_citation_validation_enabled", False)
    payment = await processed_document(client, DEFAULT_PAGES, filename="payment.pdf")
    roadmap = await processed_document(
        client,
        [
            "Project Atlas payment terms. The customer must pay invoices within thirty days "
            "of the invoice date."
        ],
        filename="roadmap.pdf",
    )
    chat_provider.response = "Payment is due within 30 days [S1, S2]."

    chat = await client.post(
        "/api/v1/chat",
        json={
            "question": "payment terms",
            "document_ids": [payment["id"], roadmap["id"]],
        },
    )
    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert len(body["citations"]) == 2
    assert all(citation["available"] is True for citation in body["citations"])

    deleted = await client.delete(f"/api/v1/documents/{payment['id']}")
    assert deleted.status_code == 204

    detail = await client.get(f"/api/v1/conversations/{body['conversation_id']}")
    citations = detail.json()["messages"][-1]["citations"]
    availability = {citation["document_id"]: citation["available"] for citation in citations}
    assert availability[payment["id"]] is False
    assert availability[roadmap["id"]] is True
    assert all(citation["document_name"] for citation in citations)


async def test_live_chat_citations_are_available(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    await processed_document(client)
    chat_provider.response = "Payment is due within 30 days [S1]."

    response = await client.post("/api/v1/chat", json={"question": "payment terms"})

    assert response.status_code == 200, response.text
    assert response.json()["citations"]
    assert all(citation["available"] is True for citation in response.json()["citations"])

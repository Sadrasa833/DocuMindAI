from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from httpx import AsyncClient, Response
from sqlalchemy import func, select

from app.api.v1.endpoints.chat import _stream_response
from app.core.exceptions import AIProviderError
from app.db.session import AsyncSessionLocal
from app.models.conversation import Conversation
from app.services.rag import (
    INSUFFICIENT_CONTEXT_ANSWER,
    STREAM_EMPTY_MESSAGE,
    STREAM_UNAVAILABLE_MESSAGE,
    RAGService,
)
from app.services.search import SearchService
from tests.fakes import DEFAULT_PAGES, make_embedding_service, register_user


async def _events(response: Response) -> AsyncIterator[dict[str, object]]:
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            yield json.loads(line[6:])


async def _collect(response: Response) -> list[dict[str, object]]:
    return [event async for event in _events(response)]


async def _stream(
    client: AsyncClient,
    question: str,
    *,
    document_ids: list[str] | None = None,
    conversation_id: str | None = None,
):
    return client.stream(
        "POST",
        "/api/v1/chat/stream",
        json={
            "question": question,
            "document_ids": document_ids or [],
            "conversation_id": conversation_id,
        },
    )


async def test_streaming_chat_emits_deltas_citations_and_persists_complete_answer(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    document = await processed_document(client, DEFAULT_PAGES)
    chat_provider.response = "Payment is due in 30 days [S1]."
    chat_provider.stream_chunks = ["Payment is due ", "in 30 days [S1]."]

    async with await _stream(
        client,
        "What are the payment terms?",
        document_ids=[document["id"]],
    ) as response:
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.headers["x-accel-buffering"] == "no"
        events = await _collect(response)

    assert [event["type"] for event in events] == ["meta", "delta", "delta", "done"]
    assert events[0]["conversation_id"] is None
    deltas = [event["content"] for event in events if event["type"] == "delta"]
    assert "".join(deltas) == chat_provider.response

    done = events[-1]
    assert done["answer"] == chat_provider.response
    assert done["replaced"] is False
    assert done["conversation_id"]
    assert done["message_id"]
    citations = done["citations"]
    assert isinstance(citations, list) and citations
    assert citations[0]["document_id"] == document["id"]
    assert citations[0]["document_name"] == "agreement.pdf"
    assert citations[0]["page_number"] == 1
    assert citations[0]["available"] is True
    assert done["citation_validation"] == {
        "enabled": True,
        "supported_claims": 1,
        "unsupported_claims": 0,
        "refused": False,
    }

    assert len(chat_provider.calls) == 1
    assert "What are the payment terms?" in chat_provider.calls[0]["user_prompt"]

    conversation = await client.get(f"/api/v1/conversations/{done['conversation_id']}")
    assert conversation.status_code == 200
    messages = conversation.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "What are the payment terms?"
    assert messages[1]["content"] == done["answer"]
    assert messages[1]["citations"] == citations

    chat_provider.stream_chunks = ["Renewal is automatic [S1]."]
    chat_provider.response = "Renewal is automatic [S1]."
    async with await _stream(
        client,
        "Does it renew?",
        conversation_id=str(done["conversation_id"]),
    ) as continuation:
        continuation_events = await _collect(continuation)

    assert continuation_events[0]["type"] == "meta"
    assert continuation_events[0]["conversation_id"] == done["conversation_id"]
    continuation_done = continuation_events[-1]
    assert continuation_done["conversation_id"] == done["conversation_id"]
    conversation = await client.get(f"/api/v1/conversations/{done['conversation_id']}")
    assert len(conversation.json()["messages"]) == 4


async def test_streaming_chat_replaces_unsupported_answer_with_validated_final_event(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    await processed_document(client)
    chat_provider.response = "The agreement was signed in Paris [S1]."
    chat_provider.stream_chunks = ["The agreement was ", "signed in Paris [S1]."]

    async with await _stream(client, "What are the payment terms?") as response:
        events = await _collect(response)

    assert [event["type"] for event in events] == ["meta", "delta", "delta", "done"]
    done = events[-1]
    assert done["answer"] == INSUFFICIENT_CONTEXT_ANSWER
    assert done["citations"] == []
    assert done["replaced"] is True

    conversation = await client.get(f"/api/v1/conversations/{done['conversation_id']}")
    messages = conversation.json()["messages"]
    assert messages[-1]["content"] == INSUFFICIENT_CONTEXT_ANSWER
    assert messages[-1]["citations"] is None


async def test_streaming_chat_sanitizes_citations_in_final_event(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    await processed_document(client)
    chat_provider.response = "Payment is due in 30 days [S1, S99]."
    chat_provider.stream_chunks = ["Payment is due in 30 days [S1, S99]."]

    async with await _stream(client, "What are the payment terms?") as response:
        events = await _collect(response)

    done = events[-1]
    assert done["answer"] == "Payment is due in 30 days [S1]."
    assert done["replaced"] is True
    assert len(done["citations"]) == 1


async def test_streaming_chat_without_context_streams_refusal_without_model(
    client: AsyncClient, chat_provider
) -> None:
    await register_user(client)

    async with await _stream(client, "What are the payment terms?") as response:
        events = await _collect(response)

    assert [event["type"] for event in events] == ["meta", "delta", "done"]
    assert events[1]["content"] == INSUFFICIENT_CONTEXT_ANSWER
    assert events[-1]["answer"] == INSUFFICIENT_CONTEXT_ANSWER
    assert events[-1]["citations"] == []
    assert chat_provider.calls == []
    conversation = await client.get(f"/api/v1/conversations/{events[-1]['conversation_id']}")
    assert len(conversation.json()["messages"]) == 2


async def test_streaming_provider_failure_does_not_persist_partial_answer(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    await processed_document(client)
    chat_provider.error = AIProviderError("secret provider detail")

    async with await _stream(client, "What are the payment terms?") as response:
        events = await _collect(response)

    assert [event["type"] for event in events] == ["meta", "error"]
    assert events[-1]["message"] == STREAM_UNAVAILABLE_MESSAGE
    assert "secret" not in str(events[-1])
    conversations = await client.get("/api/v1/conversations")
    assert conversations.json()["total"] == 0


async def test_streaming_failure_after_deltas_does_not_persist_partial_answer(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    await processed_document(client)
    chat_provider.error = AIProviderError("stream interrupted")
    chat_provider.stream_chunks = ["Partial answer "]
    chat_provider.fail_stream_after = 1

    async with await _stream(client, "What are the payment terms?") as response:
        events = await _collect(response)

    assert [event["type"] for event in events] == ["meta", "delta", "error"]
    assert events[1]["content"] == "Partial answer "
    conversations = await client.get("/api/v1/conversations")
    assert conversations.json()["total"] == 0


async def test_streaming_empty_response_is_not_persisted(
    client: AsyncClient, processed_document, chat_provider
) -> None:
    await register_user(client)
    await processed_document(client)
    chat_provider.stream_chunks = []

    async with await _stream(client, "What are the payment terms?") as response:
        events = await _collect(response)

    assert [event["type"] for event in events] == ["meta", "error"]
    assert events[-1]["message"] == STREAM_EMPTY_MESSAGE
    conversations = await client.get("/api/v1/conversations")
    assert conversations.json()["total"] == 0


async def test_streaming_chat_validates_auth_ownership_and_input(
    client_factory, processed_document, chat_provider
) -> None:
    alice = await client_factory()
    bob = await client_factory()
    await register_user(alice, email="alice@example.com")
    await register_user(bob, email="bob@example.com")
    alice_document = await processed_document(alice, DEFAULT_PAGES)
    chat_provider.response = "A private answer [S1]."

    async with await _stream(
        alice, "payment terms", document_ids=[alice_document["id"]]
    ) as response:
        alice_events = await _collect(response)
    conversation_id = alice_events[-1]["conversation_id"]

    async with await _stream(
        bob,
        "continue",
        conversation_id=str(conversation_id),
    ) as forbidden_conversation:
        response = await forbidden_conversation.aread()
        assert forbidden_conversation.status_code == 404
        assert b"not_found" in response

    async with await _stream(bob, "payment", document_ids=[alice_document["id"]]) as foreign_doc:
        response = await foreign_doc.aread()
        assert foreign_doc.status_code == 404
        assert b"not_found" in response

    blank = await alice.post("/api/v1/chat/stream", json={"question": " "})
    assert blank.status_code == 422

    anonymous = await client_factory()
    unauthenticated = await anonymous.post(
        "/api/v1/chat/stream", json={"question": "payment terms"}
    )
    assert unauthenticated.status_code == 401


async def test_streaming_cancellation_does_not_persist_partial_answer(
    client: AsyncClient,
    processed_document,
    chat_provider,
    embedding_provider,
    test_settings,
) -> None:
    user = await register_user(client)
    document = await processed_document(client)
    chat_provider.stream_chunks = ["First part ", "second part ", "third part [S1]."]
    chat_provider.response = "First part second part third part [S1]."

    async with AsyncSessionLocal() as session:
        service = RAGService(
            session,
            search_service=SearchService(
                session,
                embedding_service=make_embedding_service(embedding_provider),
                settings=test_settings,
            ),
            chat_provider=chat_provider,
            settings=test_settings,
        )
        prepared = await service.prepare_stream(
            user_id=uuid.UUID(user["id"]),
            question="What are the payment terms?",
            document_ids=[uuid.UUID(document["id"])],
        )
        events = service.stream_events(prepared)
        assert (await anext(events))["type"] == "meta"
        assert (await anext(events))["type"] == "delta"
        await events.aclose()

    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count(Conversation.id)).where(Conversation.user_id == uuid.UUID(user["id"]))
        )
    assert count == 0


async def test_streaming_endpoint_stops_after_disconnect() -> None:
    class _FakeRequest:
        def __init__(self) -> None:
            self.checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return self.checks > 1

    class _FakeService:
        def __init__(self) -> None:
            self.closed = False

        async def stream_events(self, prepared):
            try:
                yield {"type": "meta", "conversation_id": None}
                yield {"type": "delta", "content": "partial"}
                yield {"type": "done"}
            finally:
                self.closed = True

    service = _FakeService()
    chunks = [chunk async for chunk in _stream_response(service, None, _FakeRequest())]

    assert len(chunks) == 1
    assert chunks[0].startswith("event: meta")
    assert "event: done" not in "".join(chunks)
    assert service.closed is True


async def test_streaming_chat_rejects_missing_conversation(
    client: AsyncClient,
) -> None:
    await register_user(client)
    async with await _stream(
        client,
        "hello",
        conversation_id=str(uuid.uuid4()),
    ) as response:
        assert response.status_code == 404

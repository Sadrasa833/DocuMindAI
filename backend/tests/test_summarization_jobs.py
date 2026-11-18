from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.core.exceptions import AIProviderError, SummarizationError
from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.models.enums import SummaryStatus, SummaryStyle
from app.repositories.documents import DocumentRepository
from app.workers import tasks
from tests.conftest import settings
from tests.fakes import register_user


class _RetryCalled(Exception):
    pass


class _FakeTask:
    def __init__(self, retries: int = 0, max_retries: int = 3) -> None:
        self.request = SimpleNamespace(retries=retries)
        self.max_retries = max_retries
        self.retry_calls = 0

    def retry(self, *, exc: Exception, countdown: int) -> None:
        self.retry_calls += 1
        raise _RetryCalled from exc


async def _queue_summary(
    client: AsyncClient,
    document_id: str,
    dispatcher,
    style: str = "short",
    *,
    force: bool = False,
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/documents/{document_id}/summarize",
        json={"style": style, "force": force},
    )
    assert response.status_code == 202, response.text
    return dispatcher.summary_jobs[-1]


async def test_concurrent_summary_requests_enqueue_one_job(
    client: AsyncClient, processed_document, dispatcher
) -> None:
    await register_user(client)
    document = await processed_document(client)

    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/documents/{document['id']}/summarize",
                json={"style": "short"},
            )
            for _ in range(3)
        ]
    )

    assert [response.status_code for response in responses] == [202, 202, 202]
    assert all(response.json()["status"] == "pending" for response in responses)
    assert len(dispatcher.summary_jobs) == 1
    job = dispatcher.summary_jobs[0]
    async with AsyncSessionLocal() as session:
        stored = await session.get(Document, uuid.UUID(document["id"]))
        assert stored is not None
        assert stored.summary_status == SummaryStatus.PENDING
        assert stored.summary_attempt_id == job["attempt_id"]

    repeated = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "short"},
    )
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "pending"
    assert len(dispatcher.summary_jobs) == 1


async def test_summary_status_none_pending_processing_and_completed(
    client: AsyncClient, processed_document, dispatcher
) -> None:
    await register_user(client)
    document = await processed_document(client)

    empty = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert empty.status_code == 200
    assert empty.json()["status"] == "none"
    assert empty.json()["summary"] is None
    assert empty.json()["style"] is None

    job = await _queue_summary(client, document["id"], dispatcher)
    pending = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert pending.json()["status"] == "pending"

    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        assert await repository.claim_summarization(
            uuid.UUID(document["id"]), attempt_id=job["attempt_id"]
        )
        assert not await repository.claim_summarization(
            uuid.UUID(document["id"]), attempt_id=job["attempt_id"]
        )
        await session.commit()

    processing = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert processing.json()["status"] == "processing"
    assert processing.json()["started_at"] is not None

    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        assert await repository.complete_summarization(
            uuid.UUID(document["id"]),
            attempt_id=job["attempt_id"],
            style=SummaryStyle(job["style"]),
            summary="Persisted summary.",
            completed_at=datetime.now(UTC),
        )
        await session.commit()

    completed = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert completed.json()["status"] == "completed"
    assert completed.json()["summary"] == "Persisted summary."
    assert completed.json()["error_message"] is None
    assert completed.json()["created_at"] is not None


async def test_summary_queue_failure_is_persisted_and_retryable(
    client: AsyncClient, processed_document, dispatcher
) -> None:
    await register_user(client)
    document = await processed_document(client)
    dispatcher.fail_summary_dispatch = True

    failed = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "short"},
    )
    assert failed.status_code == 503
    assert "queue is unavailable" in failed.json()["error"]["message"]

    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "failed"
    assert "queue is unavailable" in state.json()["error_message"]

    dispatcher.fail_summary_dispatch = False
    retried = await client.post(
        f"/api/v1/documents/{document['id']}/summarize",
        json={"style": "short"},
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"
    assert len(dispatcher.summary_jobs) == 1


async def test_ai_provider_failure_is_retryable_and_not_persisted(
    client: AsyncClient, processed_document, dispatcher, chat_provider, run_summarization
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)
    chat_provider.error = AIProviderError("upstream timeout")

    with pytest.raises(SummarizationError) as raised:
        await run_summarization(uuid.UUID(document["id"]), job["style"], job["attempt_id"])

    assert raised.value.retryable is True
    assert raised.value.attempt_id == job["attempt_id"]
    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "processing"
    assert state.json()["summary"] is None


async def test_empty_model_output_is_not_persisted(
    client: AsyncClient, processed_document, dispatcher, chat_provider, run_summarization
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)
    chat_provider.response = "   "

    with pytest.raises(SummarizationError) as raised:
        await run_summarization(uuid.UUID(document["id"]), job["style"], job["attempt_id"])

    assert raised.value.retryable is True
    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "processing"
    assert state.json()["summary"] is None


async def test_summary_retry_requeues_before_retrying(
    client: AsyncClient,
    processed_document,
    dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)

    async def failing_summary(*args: object, **kwargs: object) -> bool:
        raise SummarizationError(
            "Temporary summary failure",
            retryable=True,
            attempt_id=job["attempt_id"],
        )

    monkeypatch.setattr(tasks, "_summarize_document", failing_summary)
    fake_task = _FakeTask(retries=0, max_retries=3)
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        assert await repository.claim_summarization(
            uuid.UUID(document["id"]), attempt_id=job["attempt_id"]
        )
        await session.commit()

    with pytest.raises(_RetryCalled):
        await asyncio.to_thread(
            tasks._handle_summary_task,
            fake_task,
            document["id"],
            job["style"],
            str(job["attempt_id"]),
        )

    assert fake_task.retry_calls == 1
    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "pending"
    assert state.json()["error_message"] is None


async def test_summary_final_failure_is_persisted(
    client: AsyncClient,
    processed_document,
    dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)

    async def failing_summary(*args: object, **kwargs: object) -> bool:
        raise SummarizationError(
            "Summary content is unavailable",
            retryable=False,
            attempt_id=job["attempt_id"],
        )

    monkeypatch.setattr(tasks, "_summarize_document", failing_summary)
    fake_task = _FakeTask(retries=0, max_retries=3)

    await asyncio.to_thread(
        tasks._handle_summary_task,
        fake_task,
        document["id"],
        job["style"],
        str(job["attempt_id"]),
    )

    assert fake_task.retry_calls == 0
    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "failed"
    assert state.json()["error_message"] == "Summary content is unavailable"
    assert state.json()["summary"] is None


async def test_exhausted_summary_retries_are_marked_failed(
    client: AsyncClient,
    processed_document,
    dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)

    async def unexpected_failure(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("worker crashed internally")

    monkeypatch.setattr(tasks, "_summarize_document", unexpected_failure)
    fake_task = _FakeTask(retries=3, max_retries=3)

    await asyncio.to_thread(
        tasks._handle_summary_task,
        fake_task,
        document["id"],
        job["style"],
        str(job["attempt_id"]),
    )

    assert fake_task.retry_calls == 0
    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "failed"
    assert state.json()["summary"] is None


async def test_stale_summary_worker_cannot_overwrite_recovered_attempt(
    client: AsyncClient, processed_document, dispatcher
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)
    document_id = uuid.UUID(document["id"])

    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        assert await repository.claim_summarization(document_id, attempt_id=job["attempt_id"])
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(summary_started_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await session.commit()
        recovered = await repository.recover_stuck_summaries(stale_after_seconds=60)
        await session.commit()

    assert len(recovered) == 1
    assert recovered[0].document_id == document_id
    assert recovered[0].attempt_id != job["attempt_id"]

    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        stale_completion = await repository.complete_summarization(
            document_id,
            attempt_id=job["attempt_id"],
            style=SummaryStyle(job["style"]),
            summary="Stale summary.",
            completed_at=datetime.now(UTC),
        )
        assert stale_completion is False
        assert await repository.claim_summarization(document_id, attempt_id=recovered[0].attempt_id)
        assert await repository.complete_summarization(
            document_id,
            attempt_id=recovered[0].attempt_id,
            style=recovered[0].style,
            summary="Fresh summary.",
            completed_at=datetime.now(UTC),
        )
        await session.commit()

    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "completed"
    assert state.json()["summary"] == "Fresh summary."


async def test_recovery_task_dispatches_stuck_summaries(
    client: AsyncClient,
    processed_document,
    dispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register_user(client)
    document = await processed_document(client)
    job = await _queue_summary(client, document["id"], dispatcher)
    document_id = uuid.UUID(document["id"])

    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        assert await repository.claim_summarization(document_id, attempt_id=job["attempt_id"])
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(summary_started_at=datetime.now(UTC) - timedelta(hours=2))
        )
        await session.commit()

    monkeypatch.setattr(settings, "summary_stuck_after_seconds", 60)
    dispatched: list[tuple[object, ...]] = []

    def fake_delay(*args: object) -> None:
        dispatched.append(args)

    monkeypatch.setattr(tasks.summarize_document_task, "delay", fake_delay)

    assert await asyncio.to_thread(tasks.recover_stuck_summaries_task) == 1
    assert len(dispatched) == 1
    recovered_id, style, attempt_id = dispatched[0]
    assert recovered_id == document["id"]
    assert style == "short"
    assert uuid.UUID(str(attempt_id)) != job["attempt_id"]

    state = await client.get(f"/api/v1/documents/{document['id']}/summary")
    assert state.json()["status"] == "pending"


async def test_summary_status_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/documents/{uuid.uuid4()}/summary")
    assert response.status_code == 401

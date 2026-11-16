from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import AIProviderError, DocumentProcessingError
from app.db.session import AsyncSessionLocal
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService
from app.services.ocr import OcrOptions, OcrUnavailableError, TesseractOcrEngine
from app.services.pdf import extract_pdf_text
from app.utils.text import SourcePage, chunk_pages, clean_page_text, split_sentences
from app.workers import tasks as worker_tasks
from tests.conftest import TEST_UPLOAD_PATH, settings
from tests.fakes import (
    FakeEmbeddingProvider,
    make_embedding_service,
    make_pdf,
    register_user,
    upload_pdf,
)


async def _claim_document(document_id: uuid.UUID) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        document = await repository.claim_for_processing(
            document_id,
            stale_after_seconds=settings.processing_stuck_after_seconds,
        )
        assert document is not None
        await session.commit()
        assert document.processing_attempt_id is not None
        return document.processing_attempt_id


async def _age_processing_document(document_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        document.processing_started_at = datetime.now(UTC) - timedelta(
            seconds=settings.processing_stuck_after_seconds + 60
        )
        await session.commit()


async def _document_status(document_id: uuid.UUID) -> DocumentStatus:
    async with AsyncSessionLocal() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        return document.status


def test_pdf_extraction_preserves_page_numbers() -> None:
    pdf = make_pdf(["First page payment terms.", "Second page confidentiality terms."])
    extraction = extract_pdf_text(pdf)
    assert extraction.page_count == 2
    assert [page.page_number for page in extraction.pages] == [1, 2]
    assert "payment terms" in extraction.pages[0].text
    assert "confidentiality" in extraction.pages[1].text


def test_pdf_extraction_rejects_blank_documents() -> None:
    pdf = make_pdf([" "])
    with pytest.raises(DocumentProcessingError) as error:
        extract_pdf_text(pdf, ocr_options=OcrOptions(enabled=False))
    assert error.value.retryable is False
    assert "No selectable text" in error.value.message


class _FakeOcrEngine:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0

    def image_to_text(self, image) -> str:
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


def test_ocr_runs_only_for_pages_without_selectable_text() -> None:
    pdf = make_pdf([" ", "Second page payment terms."])
    engine = _FakeOcrEngine("متن فارسی صفحه اول")
    extraction = extract_pdf_text(pdf, ocr_engine=engine)

    assert engine.calls == 1
    assert [page.page_number for page in extraction.pages] == [1, 2]
    assert extraction.pages[0].text == "متن فارسی صفحه اول"
    assert "payment terms" in extraction.pages[1].text


def test_ocr_preserves_all_scanned_page_numbers() -> None:
    pdf = make_pdf([" ", " "])
    engine = _FakeOcrEngine("English scanned text.", "متن فارسی اسکن شده")
    extraction = extract_pdf_text(pdf, ocr_engine=engine)

    assert engine.calls == 2
    assert extraction.page_count == 2
    assert [page.page_number for page in extraction.pages] == [1, 2]
    assert extraction.pages[0].text == "English scanned text."
    assert extraction.pages[1].text == "متن فارسی اسکن شده"


def test_ocr_respects_the_page_limit() -> None:
    pdf = make_pdf([" ", " "])
    engine = _FakeOcrEngine()
    with pytest.raises(DocumentProcessingError) as error:
        extract_pdf_text(
            pdf,
            ocr_options=OcrOptions(max_pages=1),
            ocr_engine=engine,
        )
    assert error.value.retryable is False
    assert "exceeds the configured limit" in error.value.message
    assert engine.calls == 0


def test_ocr_reports_an_unavailable_engine() -> None:
    class _UnavailableEngine:
        def image_to_text(self, image) -> str:
            raise OcrUnavailableError("OCR engine is not available")

    pdf = make_pdf([" "])
    with pytest.raises(DocumentProcessingError) as error:
        extract_pdf_text(pdf, ocr_engine=_UnavailableEngine())
    assert error.value.retryable is False
    assert "OCR engine is not available" in error.value.message


def test_ocr_failures_are_retryable() -> None:
    class _BrokenEngine:
        def image_to_text(self, image) -> str:
            raise RuntimeError("temporary OCR failure")

    pdf = make_pdf([" "])
    with pytest.raises(DocumentProcessingError) as error:
        extract_pdf_text(pdf, ocr_engine=_BrokenEngine())
    assert error.value.retryable is True
    assert "OCR failed on page 1" in error.value.message


def test_pdf_extraction_rejects_corrupt_data() -> None:
    with pytest.raises(DocumentProcessingError):
        extract_pdf_text(b"not a PDF")


def test_clean_text_and_sentence_splitting() -> None:
    cleaned = clean_page_text("Docu-\nment\u00a0  text.\r\nSecond\tline.  ")
    assert cleaned == "Document text. Second line."
    assert split_sentences("First sentence. Second sentence? Third!") == [
        "First sentence.",
        "Second sentence?",
        "Third!",
    ]


def test_chunking_is_bounded_and_keeps_page_metadata() -> None:
    pages = [
        SourcePage(page_number=1, text="First sentence. " * 20),
        SourcePage(page_number=2, text="Second page sentence. " * 20),
    ]
    chunks = chunk_pages(pages, chunk_size=120, chunk_overlap=30)
    assert len(chunks) > 2
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.content) <= 120 for chunk in chunks)
    assert {chunk.page_number for chunk in chunks} == {1, 2}
    assert any(
        left.content.split(".")[0].strip() in right.content
        for left, right in zip(chunks, chunks[1:], strict=False)
        if left.page_number == right.page_number
    )


def test_chunking_handles_oversized_words() -> None:
    pages = [SourcePage(page_number=1, text="x" * 500)]
    chunks = chunk_pages(pages, chunk_size=100, chunk_overlap=10)
    assert chunks
    assert all(len(chunk.content) <= 100 for chunk in chunks)


@pytest.mark.asyncio
async def test_embedding_service_deduplicates_and_batches() -> None:
    provider = FakeEmbeddingProvider(dimensions=32)
    service = EmbeddingService(provider, dimensions=32, batch_size=2)
    vectors = await service.embed_documents(["alpha", "beta", "alpha", "gamma"])
    assert len(vectors) == 4
    assert vectors[0] == vectors[2]
    assert provider.calls == [["alpha", "beta"], ["gamma"]]
    query_vector = await service.embed_query("alpha")
    assert query_vector == vectors[0]


@pytest.mark.asyncio
async def test_embedding_service_validates_dimensions() -> None:
    class WrongDimensionsProvider:
        async def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    service = EmbeddingService(WrongDimensionsProvider(), dimensions=4)
    with pytest.raises(AIProviderError, match="unexpected dimension"):
        await service.embed_documents(["alpha"])


@pytest.mark.asyncio
async def test_full_document_processing_is_idempotent(client, process_document) -> None:
    await register_user(client)
    upload = await upload_pdf(client)
    assert upload.status_code == 201
    document_id = uuid.UUID(upload.json()["id"])

    processed = await process_document(document_id)
    assert processed is True
    detail = await client.get(f"/api/v1/documents/{document_id}")
    body = detail.json()
    assert body["status"] == "completed"
    assert body["page_count"] == 2
    assert body["chunk_count"] >= 2
    assert body["processed_at"] is not None
    assert body["error_message"] is None

    async with AsyncSessionLocal() as session:
        chunks = list(
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        assert len(chunks) == body["chunk_count"]
        assert all(len(chunk.embedding) == 1536 for chunk in chunks)
        assert all(chunk.page_number in (1, 2) for chunk in chunks)
        assert all(chunk.user_id for chunk in chunks)

    again = await process_document(document_id)
    assert again is False
    detail_again = await client.get(f"/api/v1/documents/{document_id}")
    assert detail_again.json()["chunk_count"] == body["chunk_count"]


@pytest.mark.asyncio
async def test_processing_service_reports_missing_file(client, process_document) -> None:
    await register_user(client)
    upload = await upload_pdf(client)
    document_id = uuid.UUID(upload.json()["id"])

    for path in TEST_UPLOAD_PATH.rglob("*.pdf"):
        path.unlink()

    with pytest.raises(DocumentProcessingError) as error:
        await process_document(document_id)
    assert error.value.retryable is True


def test_embedding_service_accepts_shared_provider() -> None:
    provider = FakeEmbeddingProvider(dimensions=8)
    service = make_embedding_service(provider)
    assert service.batch_size == 2
    assert service.dimensions == 8


@pytest.mark.asyncio
async def test_duplicate_workers_cannot_claim_the_same_job(client, process_document) -> None:
    await register_user(client)
    document_id = uuid.UUID((await upload_pdf(client)).json()["id"])
    await _claim_document(document_id)

    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        duplicate = await repository.claim_for_processing(
            document_id,
            stale_after_seconds=settings.processing_stuck_after_seconds,
        )
        await session.commit()
    assert duplicate is None

    assert await process_document(document_id) is False


@pytest.mark.asyncio
async def test_abandoned_processing_job_is_reclaimed_after_timeout(
    client, process_document
) -> None:
    await register_user(client)
    document_id = uuid.UUID((await upload_pdf(client)).json()["id"])
    await _claim_document(document_id)
    assert await _document_status(document_id) == DocumentStatus.PROCESSING

    assert await process_document(document_id) is False
    await _age_processing_document(document_id)

    assert await process_document(document_id) is True
    detail = await client.get(f"/api/v1/documents/{document_id}")
    assert detail.json()["status"] == "completed"
    assert detail.json()["chunk_count"] > 0


@pytest.mark.asyncio
async def test_recovery_resets_only_stale_processing_jobs(client) -> None:
    await register_user(client)
    stale_id = uuid.UUID((await upload_pdf(client, filename="stale.pdf")).json()["id"])
    fresh_id = uuid.UUID((await upload_pdf(client, filename="fresh.pdf")).json()["id"])
    await _claim_document(stale_id)
    await _claim_document(fresh_id)
    await _age_processing_document(stale_id)

    recovered = await worker_tasks._recover_stuck_documents()

    assert recovered == [stale_id]
    assert await _document_status(stale_id) == DocumentStatus.PENDING
    assert await _document_status(fresh_id) == DocumentStatus.PROCESSING


@pytest.mark.asyncio
async def test_recovery_task_dispatches_recovered_jobs(client, monkeypatch) -> None:
    await register_user(client)
    document_id = uuid.UUID((await upload_pdf(client)).json()["id"])
    await _claim_document(document_id)
    await _age_processing_document(document_id)

    dispatched: list[str] = []
    monkeypatch.setattr(
        worker_tasks.process_document_task,
        "delay",
        lambda value: dispatched.append(value),
    )

    count = await asyncio.to_thread(worker_tasks.recover_stuck_documents_task.run)

    assert count == 1
    assert dispatched == [str(document_id)]
    assert await _document_status(document_id) == DocumentStatus.PENDING


@pytest.mark.asyncio
async def test_failed_task_is_persisted_without_overwriting_newer_claims(
    client, process_document
) -> None:
    await register_user(client)
    document_id = uuid.UUID((await upload_pdf(client)).json()["id"])
    for path in TEST_UPLOAD_PATH.rglob("*.pdf"):
        path.unlink()

    with pytest.raises(DocumentProcessingError) as error:
        await process_document(document_id)
    assert error.value.retryable is True
    assert error.value.attempt_id is not None

    await worker_tasks._requeue_document(document_id, error.value.attempt_id)
    assert await _document_status(document_id) == DocumentStatus.PENDING

    await _claim_document(document_id)
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        stale_update = await repository.mark_failed(
            document_id,
            "stale worker failure",
            attempt_id=error.value.attempt_id,
        )
        await session.commit()
    assert stale_update is False
    assert await _document_status(document_id) == DocumentStatus.PROCESSING


@pytest.mark.asyncio
async def test_final_failure_marks_document_failed(client, process_document) -> None:
    await register_user(client)
    document_id = uuid.UUID((await upload_pdf(client)).json()["id"])
    for path in TEST_UPLOAD_PATH.rglob("*.pdf"):
        path.unlink()

    with pytest.raises(DocumentProcessingError) as error:
        await process_document(document_id)

    await worker_tasks._mark_document_failed(
        document_id,
        error.value.message,
        attempt_id=error.value.attempt_id,
    )

    detail = await client.get(f"/api/v1/documents/{document_id}")
    assert detail.json()["status"] == "failed"
    assert detail.json()["error_message"] == error.value.message


@pytest.mark.asyncio
async def test_unexpected_task_failure_marks_pending_document_failed(client, monkeypatch) -> None:
    await register_user(client)
    document_id = uuid.UUID((await upload_pdf(client)).json()["id"])

    async def fail_before_claim(_: uuid.UUID) -> None:
        raise RuntimeError("provider initialization failed")

    monkeypatch.setattr(worker_tasks, "_process_document", fail_before_claim)
    monkeypatch.setattr(worker_tasks.process_document_task, "max_retries", 0)

    await asyncio.to_thread(worker_tasks.process_document_task.run, str(document_id))

    detail = await client.get(f"/api/v1/documents/{document_id}")
    assert detail.json()["status"] == "failed"
    assert "internal error" in detail.json()["error_message"].lower()


@pytest.mark.asyncio
async def test_processing_falls_back_to_ocr_for_scanned_pdf(
    client, process_document, monkeypatch
) -> None:
    await register_user(client)
    upload = await upload_pdf(client, [" "])
    document_id = uuid.UUID(upload.json()["id"])

    persian_text = "این قرارداد پرداخت مبلغ را ظرف سی روز الزامی می‌کند"
    monkeypatch.setattr(
        TesseractOcrEngine,
        "image_to_text",
        lambda self, image: persian_text,
    )

    assert await process_document(document_id) is True
    detail = await client.get(f"/api/v1/documents/{document_id}")
    body = detail.json()
    assert body["status"] == "completed"
    assert body["page_count"] == 1
    assert body["chunk_count"] >= 1

    async with AsyncSessionLocal() as session:
        chunk = (
            await session.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
        ).scalar_one()
        assert "پرداخت" in chunk.content
        assert chunk.page_number == 1

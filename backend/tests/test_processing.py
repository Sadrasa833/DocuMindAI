from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.exceptions import AIProviderError, DocumentProcessingError
from app.db.session import AsyncSessionLocal
from app.models.chunk import DocumentChunk
from app.services.embeddings import EmbeddingService
from app.services.pdf import extract_pdf_text
from app.utils.text import SourcePage, chunk_pages, clean_page_text, split_sentences
from tests.conftest import TEST_UPLOAD_PATH
from tests.fakes import (
    FakeEmbeddingProvider,
    make_embedding_service,
    make_pdf,
    register_user,
    upload_pdf,
)


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
        extract_pdf_text(pdf)
    assert error.value.retryable is False
    assert "No selectable text" in error.value.message


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

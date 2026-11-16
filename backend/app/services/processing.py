from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AIProviderError, DocumentProcessingError
from app.models.chunk import DocumentChunk
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService
from app.services.ocr import OcrOptions
from app.services.pdf import extract_pdf_text
from app.services.storage import StorageBackend, StorageError
from app.utils.text import SourcePage, chunk_pages

logger = logging.getLogger(__name__)


class ProcessingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        storage: StorageBackend,
        embedding_service: EmbeddingService,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.embedding_service = embedding_service
        self.documents = DocumentRepository(session)
        self.chunks = ChunkRepository(session)
        self.claimed_attempt_id: uuid.UUID | None = None

    async def process(self, document_id: uuid.UUID) -> bool:
        """Process one PDF. Returns False when the task is an idempotent no-op."""
        document = await self.documents.claim_for_processing(
            document_id,
            stale_after_seconds=self.settings.processing_stuck_after_seconds,
        )
        if document is None:
            logger.info("Skipping document %s: already processing or completed", document_id)
            return False
        attempt_id = document.processing_attempt_id
        self.claimed_attempt_id = attempt_id
        await self.session.commit()

        try:
            data = await self.storage.read_bytes(document.storage_key)
            extraction = await asyncio.to_thread(
                extract_pdf_text,
                data,
                ocr_options=OcrOptions(
                    enabled=self.settings.ocr_enabled,
                    languages=self.settings.ocr_languages,
                    dpi=self.settings.ocr_dpi,
                    max_pages=self.settings.ocr_max_pages,
                    timeout_seconds=self.settings.ocr_timeout_seconds,
                ),
            )
            source_pages = [
                SourcePage(page_number=page.page_number, text=page.text)
                for page in extraction.pages
            ]
            text_chunks = chunk_pages(
                source_pages,
                chunk_size=self.settings.chunk_size,
                chunk_overlap=self.settings.chunk_overlap,
            )
            if not text_chunks:
                raise DocumentProcessingError(
                    "No usable text could be extracted from this PDF", retryable=False
                )

            embeddings = await self.embedding_service.embed_documents(
                [chunk.content for chunk in text_chunks]
            )
            stored_chunks = [
                DocumentChunk(
                    document_id=document.id,
                    user_id=document.user_id,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    chunk_index=chunk.chunk_index,
                    embedding=embedding,
                )
                for chunk, embedding in zip(text_chunks, embeddings, strict=True)
            ]
            await self.chunks.replace_for_document(document.id, stored_chunks)
            completed = await self.documents.complete_processing(
                document.id,
                attempt_id=attempt_id,
                page_count=extraction.page_count,
                chunk_count=len(stored_chunks),
                processed_at=datetime.now(UTC),
            )
            if not completed:
                await self.session.rollback()
                logger.warning(
                    "Discarding result because the processing claim was superseded",
                    extra={"document_id": str(document_id)},
                )
                return False
            await self.session.commit()
            logger.info(
                "Document processed successfully",
                extra={
                    "document_id": str(document_id),
                    "page_count": extraction.page_count,
                    "chunk_count": len(stored_chunks),
                },
            )
            return True
        except DocumentProcessingError as exc:
            await self.session.rollback()
            exc.attempt_id = attempt_id
            raise
        except AIProviderError as exc:
            await self.session.rollback()
            raise DocumentProcessingError(
                "The embedding service is temporarily unavailable",
                retryable=True,
                attempt_id=attempt_id,
            ) from exc
        except StorageError as exc:
            await self.session.rollback()
            raise DocumentProcessingError(
                "The stored document is unavailable",
                retryable=True,
                attempt_id=attempt_id,
            ) from exc
        except Exception as exc:
            await self.session.rollback()
            logger.exception("Unexpected document processing failure for %s", document_id)
            raise DocumentProcessingError(
                "Document processing failed due to an internal error",
                retryable=True,
                attempt_id=attempt_id,
            ) from exc

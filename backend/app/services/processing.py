from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AIProviderError, DocumentProcessingError
from app.models.chunk import DocumentChunk
from app.models.enums import DocumentStatus
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService
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

    async def process(self, document_id: uuid.UUID) -> bool:
        """Process one PDF. Returns False when the task is an idempotent no-op."""
        document = await self.documents.claim_for_processing(document_id)
        if document is None:
            logger.info("Skipping document %s: already processing or completed", document_id)
            return False
        await self.session.commit()

        try:
            data = await self.storage.read_bytes(document.storage_key)
            extraction = await asyncio.to_thread(extract_pdf_text, data)
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
            document.status = DocumentStatus.COMPLETED
            document.error_message = None
            document.page_count = extraction.page_count
            document.chunk_count = len(stored_chunks)
            document.processed_at = datetime.now(UTC)
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
        except DocumentProcessingError:
            await self.session.rollback()
            raise
        except AIProviderError as exc:
            await self.session.rollback()
            raise DocumentProcessingError(
                "The embedding service is temporarily unavailable", retryable=True
            ) from exc
        except StorageError as exc:
            await self.session.rollback()
            raise DocumentProcessingError(
                "The stored document is unavailable", retryable=True
            ) from exc
        except Exception as exc:
            await self.session.rollback()
            logger.exception("Unexpected document processing failure for %s", document_id)
            raise DocumentProcessingError(
                "Document processing failed due to an internal error", retryable=True
            ) from exc

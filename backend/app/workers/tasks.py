from __future__ import annotations

import asyncio
import logging
import uuid

from app.ai.openai_provider import OpenAIEmbeddingProvider
from app.core.config import get_settings
from app.core.exceptions import DocumentProcessingError
from app.db.session import AsyncSessionLocal
from app.models.enums import DocumentStatus
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService
from app.services.processing import ProcessingService
from app.services.storage import LocalStorageBackend
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _set_document_status(
    document_id: uuid.UUID, status: DocumentStatus, error_message: str | None = None
) -> None:
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        await repository.set_status(document_id, status, error_message=error_message)
        await session.commit()


async def _process_document(document_id: uuid.UUID) -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        service = ProcessingService(
            session,
            settings=settings,
            storage=LocalStorageBackend(settings),
            embedding_service=EmbeddingService(
                OpenAIEmbeddingProvider(settings),
                dimensions=settings.embedding_dimensions,
                batch_size=settings.embedding_batch_size,
            ),
        )
        await service.process(document_id)


@celery_app.task(bind=True, max_retries=3, name="documind.process_document")
def process_document_task(self, document_id: str) -> None:
    try:
        parsed_id = uuid.UUID(document_id)
    except (TypeError, ValueError):
        logger.error("Rejecting document task with invalid id: %r", document_id)
        return

    try:
        asyncio.run(_process_document(parsed_id))
    except DocumentProcessingError as exc:
        if exc.retryable and self.request.retries < self.max_retries:
            logger.warning(
                "Retrying document processing",
                extra={
                    "document_id": document_id,
                    "attempt": self.request.retries + 1,
                    "reason": exc.message,
                },
            )
            try:
                asyncio.run(_set_document_status(parsed_id, DocumentStatus.PENDING))
            except Exception:
                logger.exception("Could not reset document status before retry")
            raise self.retry(exc=exc, countdown=min(60, 5 * (self.request.retries + 1))) from exc
        logger.error(
            "Document processing failed",
            extra={"document_id": document_id, "reason": exc.message},
        )
        try:
            asyncio.run(_set_document_status(parsed_id, DocumentStatus.FAILED, exc.message))
        except Exception:
            logger.exception("Could not persist document failure status")
    except Exception as exc:
        logger.exception(
            "Unexpected document processing task failure",
            extra={"document_id": document_id},
        )
        if self.request.retries < self.max_retries:
            try:
                asyncio.run(_set_document_status(parsed_id, DocumentStatus.PENDING))
            except Exception:
                logger.exception("Could not reset document status before retry")
            raise self.retry(exc=exc, countdown=min(60, 5 * (self.request.retries + 1))) from exc
        try:
            asyncio.run(
                _set_document_status(
                    parsed_id,
                    DocumentStatus.FAILED,
                    "Document processing failed due to an internal error. Please try again.",
                )
            )
        except Exception:
            logger.exception("Could not persist document failure status")

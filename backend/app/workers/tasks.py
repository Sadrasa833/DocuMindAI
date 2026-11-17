from __future__ import annotations

import asyncio
import logging
import uuid

from app.ai.openai_provider import OpenAIEmbeddingProvider
from app.core.config import get_settings
from app.core.exceptions import DocumentProcessingError
from app.db.session import AsyncSessionLocal
from app.models.enums import DocumentStatus
from app.repositories.account_tokens import AccountTokenRepository
from app.repositories.documents import DocumentRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.services.embeddings import EmbeddingService
from app.services.processing import ProcessingService
from app.services.storage import create_storage_backend
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_QUEUE_UNAVAILABLE_MESSAGE = "The processing queue is unavailable. Please retry the document later."


async def _requeue_document(document_id: uuid.UUID, attempt_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        await repository.requeue_for_retry(document_id, attempt_id=attempt_id)
        await session.commit()


async def _mark_document_failed(
    document_id: uuid.UUID,
    error_message: str,
    *,
    attempt_id: uuid.UUID | None = None,
    statuses: tuple[DocumentStatus, ...] = (DocumentStatus.PROCESSING,),
) -> None:
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        await repository.mark_failed(
            document_id,
            error_message,
            attempt_id=attempt_id,
            statuses=statuses,
        )
        await session.commit()


async def _cleanup_expired_tokens() -> tuple[int, int]:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        account_tokens = await AccountTokenRepository(session).delete_stale(
            retention_days=settings.token_cleanup_retention_days
        )
        refresh_tokens = await RefreshTokenRepository(session).delete_stale(
            retention_days=settings.token_cleanup_retention_days
        )
        await session.commit()
        return account_tokens, refresh_tokens


async def _recover_stuck_documents() -> list[uuid.UUID]:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        document_ids = await repository.recover_stuck_documents(
            stale_after_seconds=settings.processing_stuck_after_seconds
        )
        await session.commit()
        return document_ids


async def _process_document(document_id: uuid.UUID) -> None:
    settings = get_settings()
    provider = OpenAIEmbeddingProvider(settings)
    try:
        async with AsyncSessionLocal() as session:
            service = ProcessingService(
                session,
                settings=settings,
                storage=create_storage_backend(settings),
                embedding_service=EmbeddingService(
                    provider,
                    dimensions=settings.embedding_dimensions,
                    batch_size=settings.embedding_batch_size,
                ),
            )
            try:
                await service.process(document_id)
            except DocumentProcessingError as exc:
                if exc.attempt_id is None:
                    exc.attempt_id = service.claimed_attempt_id
                raise
            except Exception as exc:
                raise DocumentProcessingError(
                    "Document processing failed due to an internal error",
                    retryable=True,
                    attempt_id=service.claimed_attempt_id,
                ) from exc
    finally:
        await provider.aclose()


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
            if exc.attempt_id is not None:
                try:
                    asyncio.run(_requeue_document(parsed_id, exc.attempt_id))
                except Exception:
                    logger.exception("Could not reset document status before retry")
            raise self.retry(exc=exc, countdown=min(60, 5 * (self.request.retries + 1))) from exc
        logger.error(
            "Document processing failed",
            extra={"document_id": document_id, "reason": exc.message},
        )
        try:
            asyncio.run(_mark_document_failed(parsed_id, exc.message, attempt_id=exc.attempt_id))
        except Exception:
            logger.exception("Could not persist document failure status")
    except Exception as exc:
        logger.exception(
            "Unexpected document processing task failure",
            extra={"document_id": document_id},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(60, 5 * (self.request.retries + 1))) from exc
        try:
            asyncio.run(
                _mark_document_failed(
                    parsed_id,
                    "Document processing failed due to an internal error. Please try again.",
                    statuses=(DocumentStatus.PENDING,),
                )
            )
        except Exception:
            logger.exception("Could not persist document failure status")


@celery_app.task(name="documind.cleanup_expired_tokens")
def cleanup_expired_tokens_task() -> dict[str, int]:
    account_tokens, refresh_tokens = asyncio.run(_cleanup_expired_tokens())
    logger.info(
        "Cleaned up expired authentication tokens",
        extra={"account_tokens": account_tokens, "refresh_tokens": refresh_tokens},
    )
    return {"account_tokens": account_tokens, "refresh_tokens": refresh_tokens}


@celery_app.task(name="documind.recover_stuck_documents")
def recover_stuck_documents_task() -> int:
    document_ids = asyncio.run(_recover_stuck_documents())
    dispatched = 0
    for document_id in document_ids:
        try:
            process_document_task.delay(str(document_id))
            dispatched += 1
        except Exception:
            logger.exception(
                "Could not enqueue recovered document",
                extra={"document_id": str(document_id)},
            )
            try:
                asyncio.run(
                    _mark_document_failed(
                        document_id,
                        _QUEUE_UNAVAILABLE_MESSAGE,
                        statuses=(DocumentStatus.PENDING,),
                    )
                )
            except Exception:
                logger.exception("Could not persist recovered document failure")
    if document_ids:
        logger.info(
            "Recovered stuck document tasks",
            extra={"recovered": len(document_ids), "dispatched": dispatched},
        )
    return dispatched

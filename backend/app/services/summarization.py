from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatProvider
from app.ai.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    build_single_summary_prompt,
    build_summary_map_prompt,
    build_summary_reduce_prompt,
)
from app.core.config import Settings
from app.core.exceptions import (
    AIProviderError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    SummarizationError,
)
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus, SummaryStatus, SummaryStyle
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.services.dispatch import TaskDispatcher

logger = logging.getLogger(__name__)

_QUEUE_UNAVAILABLE_MESSAGE = "The summary queue is unavailable. Please retry later."


@dataclass(slots=True)
class SummaryResult:
    document_id: uuid.UUID
    style: SummaryStyle | None
    status: SummaryStatus
    summary: str | None
    error_message: str | None
    started_at: datetime | None
    created_at: datetime | None
    updated_at: datetime


@dataclass(slots=True)
class _Group:
    chunks: list[DocumentChunk]

    @property
    def text(self) -> str:
        return "\n\n".join(chunk.content for chunk in self.chunks)

    @property
    def page_range(self) -> str:
        first = self.chunks[0].page_number
        last = self.chunks[-1].page_number
        return str(first) if first == last else f"{first}-{last}"


def _group_chunks(chunks: list[DocumentChunk], max_chars: int) -> list[_Group]:
    groups: list[_Group] = []
    current: list[DocumentChunk] = []
    current_length = 0
    for chunk in chunks:
        added = len(chunk.content) + (2 if current else 0)
        if current and current_length + added > max_chars:
            groups.append(_Group(chunks=current))
            current = [chunk]
            current_length = len(chunk.content)
        else:
            current.append(chunk)
            current_length += added
    if current:
        groups.append(_Group(chunks=current))
    return groups


def _group_summaries(summaries: list[str], max_chars: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    for summary in summaries:
        added = len(summary) + (2 if current else 0)
        if current and current_length + added > max_chars:
            groups.append(current)
            current = [summary]
            current_length = len(summary)
        else:
            current.append(summary)
            current_length += added
    if current:
        groups.append(current)
    return groups


class SummarizationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        chat_provider: ChatProvider,
        dispatcher: TaskDispatcher | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.chat_provider = chat_provider
        self.dispatcher = dispatcher
        self.documents = DocumentRepository(session)
        self.chunks = ChunkRepository(session)

    @staticmethod
    def _to_result(document: Document) -> SummaryResult:
        return SummaryResult(
            document_id=document.id,
            style=document.summary_style,
            status=document.summary_status,
            summary=document.summary,
            error_message=document.summary_error,
            started_at=document.summary_started_at,
            created_at=document.summarized_at,
            updated_at=document.updated_at,
        )

    async def _map_reduce(self, chunks: list[DocumentChunk], style: SummaryStyle) -> str:
        groups = _group_chunks(chunks, self.settings.summary_map_chunk_chars)
        if len(groups) == 1:
            return await self.chat_provider.generate(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_prompt=build_single_summary_prompt(groups[0].text, style.value),
                max_tokens=self.settings.summary_max_tokens,
                temperature=0.2,
            )

        partials: list[str] = []
        for group in groups:
            partials.append(
                await self.chat_provider.generate(
                    system_prompt=SUMMARY_SYSTEM_PROMPT,
                    user_prompt=build_summary_map_prompt(group.text, group.page_range),
                    max_tokens=500,
                    temperature=0.1,
                )
            )

        while len(partials) > 1:
            batches = _group_summaries(partials, self.settings.summary_reduce_chunk_chars)
            if len(batches) == len(partials):
                batches = [partials[index : index + 2] for index in range(0, len(partials), 2)]
            merged: list[str] = []
            for batch in batches:
                merged.append(
                    await self.chat_provider.generate(
                        system_prompt=SUMMARY_SYSTEM_PROMPT,
                        user_prompt=build_summary_reduce_prompt(
                            "\n\n---\n\n".join(batch), style.value
                        ),
                        max_tokens=self.settings.summary_max_tokens,
                        temperature=0.1,
                    )
                )
            partials = merged

        return partials[0]

    async def get_state(self, *, user_id: uuid.UUID, document_id: uuid.UUID) -> SummaryResult:
        document = await self.documents.get_owned(document_id, user_id)
        if document is None:
            raise NotFoundError("Document not found")
        return self._to_result(document)

    async def request_summary(
        self,
        *,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        style: SummaryStyle,
        force: bool = False,
    ) -> tuple[SummaryResult, bool]:
        """Queue asynchronous summarization and return the current state.

        The boolean indicates whether a new Celery job was enqueued. Concurrent
        or repeated requests while a job is active observe the existing job.
        """
        if self.dispatcher is None:
            raise RuntimeError("A task dispatcher is required to request summaries")

        document = await self.documents.get_owned(document_id, user_id)
        if document is None:
            raise NotFoundError("Document not found")
        if document.status != DocumentStatus.COMPLETED:
            raise ConflictError("The document must finish processing before it can be summarized")

        cached = (
            not force
            and document.summary_status == SummaryStatus.COMPLETED
            and document.summary is not None
            and document.summary_style == style
        )
        if cached:
            return self._to_result(document), False

        attempt_id = uuid.uuid4()
        marked = await self.documents.mark_summary_pending(
            document_id,
            user_id,
            style=style,
            force=force,
            attempt_id=attempt_id,
            stale_after_seconds=self.settings.summary_stuck_after_seconds,
        )
        await self.session.commit()
        await self.session.refresh(document)
        if not marked:
            return self._to_result(document), False

        try:
            await self.dispatcher.enqueue_document_summarization(
                document_id,
                style=style,
                attempt_id=attempt_id,
                force=force,
            )
        except Exception:
            logger.exception(
                "Could not enqueue document summary",
                extra={"document_id": str(document_id)},
            )
            await self.documents.mark_summary_failed(
                document_id,
                _QUEUE_UNAVAILABLE_MESSAGE,
                attempt_id=attempt_id,
                statuses=(SummaryStatus.PENDING,),
            )
            await self.session.commit()
            await self.session.refresh(document)
            raise ServiceUnavailableError(_QUEUE_UNAVAILABLE_MESSAGE) from None

        return self._to_result(document), True

    async def execute(
        self,
        *,
        document_id: uuid.UUID,
        style: SummaryStyle,
        attempt_id: uuid.UUID,
    ) -> bool:
        """Run a claimed summary attempt inside a Celery worker."""
        claimed = await self.documents.claim_summarization(document_id, attempt_id=attempt_id)
        await self.session.commit()
        if not claimed:
            logger.info(
                "Skipping summary job that is no longer claimed",
                extra={"document_id": str(document_id), "attempt_id": str(attempt_id)},
            )
            return False

        document = await self.session.get(Document, document_id)
        if document is None:
            return False

        chunks = await self.chunks.list_for_document(document.id, document.user_id)
        if not chunks:
            raise SummarizationError(
                "No extracted content is available for this document",
                retryable=False,
                attempt_id=attempt_id,
            )

        try:
            summary = await self._map_reduce(chunks, style)
        except AIProviderError as exc:
            raise SummarizationError(
                exc.message,
                retryable=True,
                attempt_id=attempt_id,
            ) from exc

        cleaned = summary.strip()
        if not cleaned:
            raise SummarizationError(
                "The AI model returned an empty summary",
                retryable=True,
                attempt_id=attempt_id,
            )

        completed = await self.documents.complete_summarization(
            document_id,
            attempt_id=attempt_id,
            style=style,
            summary=cleaned,
            completed_at=datetime.now(UTC),
        )
        await self.session.commit()
        if not completed:
            logger.warning(
                "Discarding summary from a stale worker attempt",
                extra={"document_id": str(document_id), "attempt_id": str(attempt_id)},
            )
        return completed

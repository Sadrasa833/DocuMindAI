from __future__ import annotations

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
from app.core.exceptions import ConflictError, NotFoundError
from app.models.chunk import DocumentChunk
from app.models.enums import DocumentStatus, SummaryStyle
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository


@dataclass(slots=True)
class SummaryResult:
    document_id: uuid.UUID
    style: SummaryStyle
    summary: str
    created_at: datetime


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
    ) -> None:
        self.session = session
        self.settings = settings
        self.chat_provider = chat_provider
        self.documents = DocumentRepository(session)
        self.chunks = ChunkRepository(session)

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

    async def summarize(
        self,
        *,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        style: SummaryStyle,
        force: bool = False,
    ) -> SummaryResult:
        document = await self.documents.get_owned(document_id, user_id)
        if document is None:
            raise NotFoundError("Document not found")
        if document.status != DocumentStatus.COMPLETED:
            raise ConflictError("The document must finish processing before it can be summarized")
        if document.summary and document.summary_style == style and not force:
            return SummaryResult(
                document_id=document.id,
                style=style,
                summary=document.summary,
                created_at=document.summarized_at or datetime.now(UTC),
            )

        chunks = await self.chunks.list_for_document(document.id, user_id)
        if not chunks:
            raise ConflictError("No extracted content is available for this document")
        summary = await self._map_reduce(chunks, style)
        now = datetime.now(UTC)
        document.summary = summary
        document.summary_style = style
        document.summarized_at = now
        await self.session.commit()
        return SummaryResult(
            document_id=document.id,
            style=style,
            summary=summary,
            created_at=now,
        )

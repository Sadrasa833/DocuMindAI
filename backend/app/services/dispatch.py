from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Protocol

from app.models.enums import SummaryStyle

logger = logging.getLogger(__name__)


class TaskDispatcher(Protocol):
    async def enqueue_document_processing(self, document_id: uuid.UUID) -> None: ...

    async def enqueue_document_summarization(
        self,
        document_id: uuid.UUID,
        *,
        style: SummaryStyle,
        attempt_id: uuid.UUID,
        force: bool = False,
    ) -> None: ...


class CeleryTaskDispatcher:
    async def enqueue_document_processing(self, document_id: uuid.UUID) -> None:
        from app.workers.tasks import process_document_task

        await asyncio.to_thread(process_document_task.delay, str(document_id))

    async def enqueue_document_summarization(
        self,
        document_id: uuid.UUID,
        *,
        style: SummaryStyle,
        attempt_id: uuid.UUID,
        force: bool = False,
    ) -> None:
        from app.workers.tasks import summarize_document_task

        await asyncio.to_thread(
            summarize_document_task.delay,
            str(document_id),
            style.value,
            str(attempt_id),
            force,
        )

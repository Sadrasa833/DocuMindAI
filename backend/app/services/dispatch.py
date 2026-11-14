from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Protocol

logger = logging.getLogger(__name__)


class TaskDispatcher(Protocol):
    async def enqueue_document_processing(self, document_id: uuid.UUID) -> None: ...


class CeleryTaskDispatcher:
    async def enqueue_document_processing(self, document_id: uuid.UUID) -> None:
        from app.workers.tasks import process_document_task

        await asyncio.to_thread(process_document_task.delay, str(document_id))

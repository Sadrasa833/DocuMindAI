from __future__ import annotations

import logging
import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.repositories.documents import DocumentRepository
from app.services.dispatch import TaskDispatcher
from app.services.storage import StorageBackend, StorageError
from app.utils.uploads import validate_and_spool_upload

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        storage: StorageBackend,
        dispatcher: TaskDispatcher,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.dispatcher = dispatcher
        self.documents = DocumentRepository(session)

    async def upload(self, *, user_id: uuid.UUID, file: UploadFile) -> Document:
        upload = await validate_and_spool_upload(file, self.settings.max_upload_size_bytes)
        document_id = uuid.uuid4()
        storage_key = f"{user_id}/{document_id}.pdf"
        try:
            await self.storage.save_file(upload.temporary_path, storage_key)
            document = Document(
                id=document_id,
                user_id=user_id,
                original_filename=upload.filename,
                storage_key=storage_key,
                mime_type=upload.content_type,
                file_size=upload.size,
                file_hash=upload.checksum,
                status=DocumentStatus.UPLOADED,
            )
            await self.documents.add(document)
            await self.session.commit()
            await self.session.refresh(document)
        except Exception:
            await self.session.rollback()
            try:
                await self.storage.delete(storage_key)
            except StorageError:
                logger.warning("Could not clean up stored file after upload failure")
            raise
        finally:
            upload.cleanup()

        document.status = DocumentStatus.PENDING
        await self.session.commit()
        try:
            await self.dispatcher.enqueue_document_processing(document.id)
        except Exception:
            logger.exception("Could not enqueue document for processing")
            document.status = DocumentStatus.FAILED
            document.error_message = "The processing queue is unavailable. Please retry later."
            await self.session.commit()
        await self.session.refresh(document)
        return document

    async def get(self, document_id: uuid.UUID, user_id: uuid.UUID) -> Document:
        document = await self.documents.get_owned(document_id, user_id)
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def list(
        self,
        user_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        status: DocumentStatus | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[Document], int]:
        return await self.documents.list_owned(
            user_id,
            page=page,
            page_size=page_size,
            status=status,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def stats(self, user_id: uuid.UUID) -> dict[str, int]:
        return await self.documents.stats(user_id)

    async def delete(self, document_id: uuid.UUID, user_id: uuid.UUID) -> None:
        document = await self.get(document_id, user_id)
        storage_key = document.storage_key
        await self.documents.delete(document)
        await self.session.commit()
        try:
            await self.storage.delete(storage_key)
        except StorageError:
            logger.warning("Stored file could not be removed for document %s", document_id)

    async def status(self, document_id: uuid.UUID, user_id: uuid.UUID) -> Document:
        return await self.get(document_id, user_id)

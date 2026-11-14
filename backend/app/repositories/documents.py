from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.enums import DocumentStatus


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, document: Document) -> Document:
        self.session.add(document)
        await self.session.flush()
        return document

    async def get_owned(
        self, document_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Document | None:
        stmt = select(Document).where(Document.id == document_id, Document.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_many_owned(
        self, document_ids: Sequence[uuid.UUID], user_id: uuid.UUID
    ) -> list[Document]:
        if not document_ids:
            return []
        result = await self.session.execute(
            select(Document).where(Document.id.in_(document_ids), Document.user_id == user_id)
        )
        return list(result.scalars().all())

    def _filtered_query(
        self,
        user_id: uuid.UUID,
        *,
        status: DocumentStatus | None = None,
        search: str | None = None,
    ) -> Select[tuple[Document]]:
        stmt = select(Document).where(Document.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Document.status == status)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Document.original_filename.ilike(f"%{escaped}%", escape="\\"))
        return stmt

    async def list_owned(
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
        stmt = self._filtered_query(user_id, status=status, search=search)
        count_result = await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        total = int(count_result.scalar_one())
        sort_columns = {
            "created_at": Document.created_at,
            "original_filename": Document.original_filename,
            "file_size": Document.file_size,
        }
        column = sort_columns.get(sort_by, Document.created_at)
        stmt = stmt.order_by(column.asc() if sort_order == "asc" else column.desc(), Document.id)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def stats(self, user_id: uuid.UUID) -> dict[str, int]:
        result = await self.session.execute(
            select(
                func.count(Document.id),
                func.count(Document.id).filter(Document.status == DocumentStatus.COMPLETED),
                func.count(Document.id).filter(
                    Document.status.in_(
                        [
                            DocumentStatus.UPLOADED,
                            DocumentStatus.PENDING,
                            DocumentStatus.PROCESSING,
                        ]
                    )
                ),
                func.count(Document.id).filter(Document.status == DocumentStatus.FAILED),
                func.coalesce(func.sum(Document.chunk_count), 0),
                func.coalesce(func.sum(Document.page_count), 0),
            ).where(Document.user_id == user_id)
        )
        row = result.one()
        return {
            "total_documents": int(row[0]),
            "completed_documents": int(row[1]),
            "processing_documents": int(row[2]),
            "failed_documents": int(row[3]),
            "total_chunks": int(row[4]),
            "total_pages": int(row[5]),
        }

    async def delete(self, document: Document) -> None:
        await self.session.delete(document)

    async def claim_for_processing(self, document_id: uuid.UUID) -> Document | None:
        """Atomically transition a queued document into processing.

        Completed or already-processing documents are skipped, which makes task
        dispatch idempotent and prevents two workers from ingesting the same file.
        """
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.status.in_(
                    [DocumentStatus.UPLOADED, DocumentStatus.PENDING, DocumentStatus.FAILED]
                ),
            )
            .values(
                status=DocumentStatus.PROCESSING,
                error_message=None,
                processing_started_at=now,
                updated_at=now,
            )
            .returning(Document.id)
        )
        claimed_id = result.scalar_one_or_none()
        if claimed_id is None:
            return None
        await self.session.flush()
        return await self.session.get(Document, claimed_id)

    async def set_status(
        self,
        document_id: uuid.UUID,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> Document | None:
        document = await self.session.get(Document, document_id)
        if document is None:
            return None
        document.status = status
        document.error_message = error_message
        if status == DocumentStatus.COMPLETED:
            document.processed_at = datetime.now(UTC)
        await self.session.flush()
        return document

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, delete, literal, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus


@dataclass(slots=True)
class SearchHit:
    chunk: DocumentChunk
    document_name: str
    similarity: float


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_document(
        self, document_id: uuid.UUID, chunks: list[DocumentChunk]
    ) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        self.session.add_all(chunks)
        await self.session.flush()

    async def delete_for_document(self, document_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

    async def list_for_document(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.user_id == user_id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def semantic_search(
        self,
        *,
        query_embedding: list[float],
        user_id: uuid.UUID,
        document_ids: Sequence[uuid.UUID] | None,
        top_k: int,
    ) -> list[SearchHit]:
        bind = self.session.get_bind()
        dialect = bind.dialect.name if bind is not None else "postgresql"
        filters = [
            DocumentChunk.user_id == user_id,
            Document.status == DocumentStatus.COMPLETED,
        ]
        if document_ids:
            filters.append(DocumentChunk.document_id.in_(document_ids))

        if dialect == "postgresql":
            distance = DocumentChunk.embedding.op("<=>")(
                literal(query_embedding, type_=Vector(get_settings().embedding_dimensions))
            )
            statement = (
                select(
                    DocumentChunk,
                    Document.original_filename,
                    type_coerce(distance, Float).label("distance"),
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(*filters)
                .order_by(distance)
                .limit(top_k)
            )
            result = await self.session.execute(statement)
            return [
                SearchHit(
                    chunk=row[0],
                    document_name=row[1],
                    similarity=max(0.0, min(1.0, 1.0 - float(row[2]))),
                )
                for row in result.all()
            ]

        statement = (
            select(DocumentChunk, Document.original_filename)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(*filters)
            .limit(2000)
        )
        result = await self.session.execute(statement)
        hits = [
            SearchHit(
                chunk=row[0],
                document_name=row[1],
                similarity=_cosine_similarity(query_embedding, row[0].embedding),
            )
            for row in result.all()
        ]
        hits.sort(key=lambda hit: hit.similarity, reverse=True)
        return hits[:top_k]

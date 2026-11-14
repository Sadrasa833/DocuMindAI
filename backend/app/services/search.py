from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models.enums import DocumentStatus
from app.repositories.chunks import ChunkRepository, SearchHit
from app.repositories.documents import DocumentRepository
from app.services.embeddings import EmbeddingService


class SearchService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_service: EmbeddingService,
        settings: Settings,
    ) -> None:
        self.documents = DocumentRepository(session)
        self.chunks = ChunkRepository(session)
        self.embedding_service = embedding_service
        self.settings = settings

    async def _validate_documents(
        self, user_id: uuid.UUID, document_ids: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        unique_ids = list(dict.fromkeys(document_ids))
        if not unique_ids:
            return []
        documents = await self.documents.get_many_owned(unique_ids, user_id)
        if len(documents) != len(unique_ids):
            raise NotFoundError("One or more selected documents were not found")
        if any(document.status != DocumentStatus.COMPLETED for document in documents):
            raise ConflictError("One or more selected documents are not ready yet")
        return unique_ids

    async def retrieve(
        self,
        *,
        user_id: uuid.UUID,
        query: str,
        document_ids: Sequence[uuid.UUID] | None = None,
        top_k: int,
    ) -> list[SearchHit]:
        validated_ids = await self._validate_documents(user_id, document_ids or [])
        embedding = await self.embedding_service.embed_query(query)
        hits = await self.chunks.semantic_search(
            query_embedding=embedding,
            user_id=user_id,
            document_ids=validated_ids,
            top_k=min(top_k, 20),
        )
        return [hit for hit in hits if hit.similarity >= self.settings.search_min_similarity]

    async def search(
        self,
        *,
        user_id: uuid.UUID,
        query: str,
        document_ids: Sequence[uuid.UUID] | None = None,
        top_k: int | None = None,
    ) -> list[SearchHit]:
        return await self.retrieve(
            user_id=user_id,
            query=query,
            document_ids=document_ids,
            top_k=top_k or self.settings.search_default_top_k,
        )

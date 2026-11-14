from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatProvider
from app.ai.prompts import RAG_SYSTEM_PROMPT, build_rag_user_prompt
from app.core.config import Settings
from app.models.conversation import Conversation
from app.models.enums import MessageRole
from app.models.message import Message
from app.repositories.chunks import SearchHit
from app.repositories.conversations import ConversationRepository
from app.services.search import SearchService

INSUFFICIENT_CONTEXT_ANSWER = (
    "I could not find enough relevant information in your documents to answer that question. "
    "Try selecting more completed documents or asking a more specific question."
)


@dataclass(slots=True)
class RagResult:
    answer: str
    conversation_id: uuid.UUID
    citations: list[dict[str, Any]]


def citations_from_hits(hits: list[SearchHit]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for hit in hits:
        marker = (str(hit.chunk.document_id), str(hit.chunk.id))
        if marker in seen:
            continue
        seen.add(marker)
        citations.append(
            {
                "document_id": str(hit.chunk.document_id),
                "document_name": hit.document_name,
                "page_number": hit.chunk.page_number,
                "chunk_id": str(hit.chunk.id),
            }
        )
    return citations


def build_sources(hits: list[SearchHit]) -> list[dict[str, Any]]:
    return [
        {
            "source_label": f"S{index}",
            "document": hit.document_name,
            "page": hit.chunk.page_number,
            "content": hit.chunk.content,
        }
        for index, hit in enumerate(hits, start=1)
    ]


class RAGService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        search_service: SearchService,
        chat_provider: ChatProvider,
        settings: Settings,
    ) -> None:
        self.session = session
        self.search_service = search_service
        self.chat_provider = chat_provider
        self.settings = settings
        self.conversations = ConversationRepository(session)

    async def _resolve_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID | None, question: str
    ) -> Conversation:
        if conversation_id is not None:
            conversation = await self.conversations.get_owned(conversation_id, user_id)
            if conversation is None:
                from app.core.exceptions import NotFoundError

                raise NotFoundError("Conversation not found")
            return conversation
        title = question if len(question) <= 80 else question[:77].rstrip() + "..."
        conversation = Conversation(user_id=user_id, title=title)
        await self.conversations.add(conversation)
        return conversation

    async def answer(
        self,
        *,
        user_id: uuid.UUID,
        question: str,
        document_ids: list[uuid.UUID] | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> RagResult:
        conversation = await self._resolve_conversation(user_id, conversation_id, question)
        history_messages = await self.conversations.recent_messages(
            conversation.id, limit=self.settings.rag_history_messages
        )
        history = [
            {"role": message.role.value, "content": message.content} for message in history_messages
        ]
        hits = await self.search_service.retrieve(
            user_id=user_id,
            query=question,
            document_ids=document_ids or [],
            top_k=self.settings.rag_max_context_chunks,
        )

        if hits:
            answer = await self.chat_provider.generate(
                system_prompt=RAG_SYSTEM_PROMPT,
                user_prompt=build_rag_user_prompt(question, build_sources(hits), history=history),
                max_tokens=900,
                temperature=0.0,
            )
            citations = citations_from_hits(hits)
        else:
            answer = INSUFFICIENT_CONTEXT_ANSWER
            citations = []

        await self.conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=question,
            )
        )
        await self.conversations.add_message(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=answer,
                citations=citations or None,
            )
        )
        await self.session.commit()
        return RagResult(
            answer=answer,
            conversation_id=conversation.id,
            citations=citations,
        )

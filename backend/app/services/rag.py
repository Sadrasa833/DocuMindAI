from __future__ import annotations

import logging
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
from app.repositories.conversations import ConversationRepository
from app.services.citation_validation import (
    build_sources,
    resolve_citations,
    validate_citations,
)
from app.services.search import SearchService

logger = logging.getLogger(__name__)

INSUFFICIENT_CONTEXT_ANSWER = (
    "I could not find enough relevant information in your documents to answer that question. "
    "Try selecting more completed documents or asking a more specific question."
)


@dataclass(slots=True)
class RagResult:
    answer: str
    conversation_id: uuid.UUID
    citations: list[dict[str, Any]]


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
            if self.settings.rag_citation_validation_enabled:
                validation = await validate_citations(
                    answer,
                    hits,
                    min_support=self.settings.rag_citation_min_support,
                    require_citations=self.settings.rag_citation_require_citations,
                    embedding_service=(
                        self.search_service.embedding_service
                        if self.settings.rag_citation_semantic_check
                        else None
                    ),
                    semantic_threshold=self.settings.rag_citation_semantic_threshold,
                )
                if validation.should_refuse:
                    logger.warning(
                        "Rejecting answer without supported citations",
                        extra={
                            "had_citations": validation.had_citations,
                            "supported_claims": validation.supported_claims,
                            "unsupported_claims": validation.unsupported_claims,
                        },
                    )
                    answer = INSUFFICIENT_CONTEXT_ANSWER
                    citations = []
                else:
                    answer = validation.answer
                    citations = validation.citations
            else:
                answer, citations = resolve_citations(answer, hits)
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

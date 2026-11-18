from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatProvider
from app.ai.prompts import RAG_SYSTEM_PROMPT, build_rag_user_prompt
from app.core.config import Settings
from app.db.session import AsyncSessionLocal
from app.models.conversation import Conversation
from app.models.enums import MessageRole
from app.models.message import Message
from app.repositories.chunks import SearchHit
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

STREAM_UNAVAILABLE_MESSAGE = "The AI service is temporarily unavailable. Please try again."
STREAM_FAILED_MESSAGE = "The AI service could not process this request. Please try again."
STREAM_EMPTY_MESSAGE = "The AI service returned an empty response. Please try again."
STREAM_SAVE_FAILED_MESSAGE = "Your answer was generated but could not be saved. Please try again."


@dataclass(slots=True)
class RagResult:
    answer: str
    conversation_id: uuid.UUID
    citations: list[dict[str, Any]]
    citation_validation: dict[str, Any] | None = None


@dataclass(slots=True)
class ValidatedAnswer:
    answer: str
    citations: list[dict[str, Any]]
    citation_validation: dict[str, Any] | None = None


@dataclass(slots=True)
class PreparedStream:
    user_id: uuid.UUID
    conversation_id: uuid.UUID | None
    question: str
    history: list[dict[str, str]]
    hits: list[SearchHit]


def _conversation_title(question: str) -> str:
    return question if len(question) <= 80 else question[:77].rstrip() + "..."


async def _persist_turn(
    session: AsyncSession,
    conversation: Conversation,
    *,
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
) -> Message:
    repository = ConversationRepository(session)
    await repository.add_message(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=question,
        )
    )
    assistant = await repository.add_message(
        Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=answer,
            citations=citations or None,
        )
    )
    await session.commit()
    return assistant


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
        conversation = Conversation(user_id=user_id, title=_conversation_title(question))
        await self.conversations.add(conversation)
        return conversation

    async def _history(self, conversation_id: uuid.UUID) -> list[dict[str, str]]:
        history_messages = await self.conversations.recent_messages(
            conversation_id, limit=self.settings.rag_history_messages
        )
        return [
            {"role": message.role.value, "content": message.content} for message in history_messages
        ]

    async def _validate_answer(self, answer: str, hits: list[SearchHit]) -> ValidatedAnswer:
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
            summary = {
                "enabled": True,
                "supported_claims": validation.supported_claims,
                "unsupported_claims": validation.unsupported_claims,
                "refused": validation.should_refuse,
            }
            if validation.should_refuse:
                logger.warning(
                    "Rejecting answer with unsupported cited claims",
                    extra={
                        "had_citations": validation.had_citations,
                        "supported_claims": validation.supported_claims,
                        "unsupported_claims": validation.unsupported_claims,
                    },
                )
                return ValidatedAnswer(
                    answer=INSUFFICIENT_CONTEXT_ANSWER,
                    citations=[],
                    citation_validation=summary,
                )
            return ValidatedAnswer(
                answer=validation.answer,
                citations=validation.citations,
                citation_validation=summary,
            )
        sanitized, citations = resolve_citations(answer, hits)
        return ValidatedAnswer(answer=sanitized, citations=citations)

    async def _generate_answer(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        hits: list[SearchHit],
    ) -> ValidatedAnswer:
        if not hits:
            return ValidatedAnswer(answer=INSUFFICIENT_CONTEXT_ANSWER, citations=[])
        answer = await self.chat_provider.generate(
            system_prompt=RAG_SYSTEM_PROMPT,
            user_prompt=build_rag_user_prompt(question, build_sources(hits), history=history),
            max_tokens=900,
            temperature=0.0,
        )
        return await self._validate_answer(answer, hits)

    async def answer(
        self,
        *,
        user_id: uuid.UUID,
        question: str,
        document_ids: list[uuid.UUID] | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> RagResult:
        conversation = await self._resolve_conversation(user_id, conversation_id, question)
        history = await self._history(conversation.id)
        hits = await self.search_service.retrieve(
            user_id=user_id,
            query=question,
            document_ids=document_ids or [],
            top_k=self.settings.rag_max_context_chunks,
        )

        validated = await self._generate_answer(
            question=question,
            history=history,
            hits=hits,
        )
        await _persist_turn(
            self.session,
            conversation,
            question=question,
            answer=validated.answer,
            citations=validated.citations,
        )
        return RagResult(
            answer=validated.answer,
            conversation_id=conversation.id,
            citations=validated.citations,
            citation_validation=validated.citation_validation,
        )

    async def prepare_stream(
        self,
        *,
        user_id: uuid.UUID,
        question: str,
        document_ids: list[uuid.UUID] | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> PreparedStream:
        resolved_id: uuid.UUID | None = None
        history: list[dict[str, str]] = []
        if conversation_id is not None:
            conversation = await self.conversations.get_owned(conversation_id, user_id)
            if conversation is None:
                from app.core.exceptions import NotFoundError

                raise NotFoundError("Conversation not found")
            resolved_id = conversation.id
            history = await self._history(conversation.id)

        hits = await self.search_service.retrieve(
            user_id=user_id,
            query=question,
            document_ids=document_ids or [],
            top_k=self.settings.rag_max_context_chunks,
        )
        return PreparedStream(
            user_id=user_id,
            conversation_id=resolved_id,
            question=question,
            history=history,
            hits=hits,
        )

    async def stream_events(self, prepared: PreparedStream) -> AsyncIterator[dict[str, Any]]:
        yield {
            "type": "meta",
            "conversation_id": (
                str(prepared.conversation_id) if prepared.conversation_id is not None else None
            ),
        }

        if not prepared.hits:
            validated = ValidatedAnswer(answer=INSUFFICIENT_CONTEXT_ANSWER, citations=[])
            streamed_answer = validated.answer
            yield {"type": "delta", "content": streamed_answer}
        else:
            parts: list[str] = []
            try:
                stream = getattr(self.chat_provider, "stream", None)
                if stream is not None:
                    async for chunk in stream(
                        system_prompt=RAG_SYSTEM_PROMPT,
                        user_prompt=build_rag_user_prompt(
                            prepared.question,
                            build_sources(prepared.hits),
                            history=prepared.history,
                        ),
                        max_tokens=900,
                        temperature=0.0,
                    ):
                        if not chunk:
                            continue
                        parts.append(chunk)
                        yield {"type": "delta", "content": chunk}
                else:
                    chunk = await self.chat_provider.generate(
                        system_prompt=RAG_SYSTEM_PROMPT,
                        user_prompt=build_rag_user_prompt(
                            prepared.question,
                            build_sources(prepared.hits),
                            history=prepared.history,
                        ),
                        max_tokens=900,
                        temperature=0.0,
                    )
                    parts.append(chunk)
                    yield {"type": "delta", "content": chunk}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Chat streaming provider failed: %s", type(exc).__name__)
                yield {"type": "error", "message": STREAM_UNAVAILABLE_MESSAGE}
                return

            streamed_answer = "".join(parts).strip()
            if not streamed_answer:
                yield {"type": "error", "message": STREAM_EMPTY_MESSAGE}
                return
            try:
                validated = await self._validate_answer(streamed_answer, prepared.hits)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Chat streaming validation failed")
                yield {"type": "error", "message": STREAM_FAILED_MESSAGE}
                return

        try:
            async with AsyncSessionLocal() as session:
                repository = ConversationRepository(session)
                if prepared.conversation_id is None:
                    conversation = Conversation(
                        user_id=prepared.user_id,
                        title=_conversation_title(prepared.question),
                    )
                    await repository.add(conversation)
                else:
                    conversation = await session.get(Conversation, prepared.conversation_id)
                    if conversation is None or conversation.user_id != prepared.user_id:
                        yield {"type": "error", "message": STREAM_FAILED_MESSAGE}
                        return
                assistant = await _persist_turn(
                    session,
                    conversation,
                    question=prepared.question,
                    answer=validated.answer,
                    citations=validated.citations,
                )
                conversation_id = conversation.id
                message_id = assistant.id
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to persist streamed chat response")
            yield {"type": "error", "message": STREAM_SAVE_FAILED_MESSAGE}
            return

        yield {
            "type": "done",
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "answer": validated.answer,
            "citations": [{**citation, "available": True} for citation in validated.citations],
            "replaced": validated.answer != streamed_answer,
            "citation_validation": validated.citation_validation,
        }

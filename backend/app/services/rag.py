from __future__ import annotations

import logging
import re
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

logger = logging.getLogger(__name__)

INSUFFICIENT_CONTEXT_ANSWER = (
    "I could not find enough relevant information in your documents to answer that question. "
    "Try selecting more completed documents or asking a more specific question."
)

_CITATION_GROUP = re.compile(r"\[([^\[\]]*)\]")
_CITATION_LABEL = re.compile(r"\bS(\d+)\b", re.IGNORECASE)
_REPEATED_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?])")


@dataclass(slots=True)
class RagResult:
    answer: str
    conversation_id: uuid.UUID
    citations: list[dict[str, Any]]


def _citation(hit: SearchHit) -> dict[str, Any]:
    return {
        "document_id": str(hit.chunk.document_id),
        "document_name": hit.document_name,
        "page_number": hit.chunk.page_number,
        "chunk_id": str(hit.chunk.id),
    }


def resolve_citations(answer: str, hits: list[SearchHit]) -> tuple[str, list[dict[str, Any]]]:
    """Keep only source labels the model actually cited and that exist.

    Source labels are assigned by :func:`build_sources` (S1..Sn). Any fabricated
    or unknown label is removed from the answer and never returned as a citation.
    """
    if not hits:
        return answer, []

    cited_indexes: list[int] = []
    seen_indexes: set[int] = set()
    available = len(hits)

    def replace_group(match: re.Match[str]) -> str:
        labels = list(_CITATION_LABEL.finditer(match.group(1)))
        if not labels:
            return match.group(0)

        valid_labels: list[str] = []
        for item in labels:
            index = int(item.group(1))
            if 1 <= index <= available:
                valid_labels.append(f"S{index}")
                if index - 1 not in seen_indexes:
                    seen_indexes.add(index - 1)
                    cited_indexes.append(index - 1)

        if valid_labels:
            return f"[{', '.join(valid_labels)}]"
        return ""

    sanitized = _CITATION_GROUP.sub(replace_group, answer)
    sanitized = _REPEATED_SPACES.sub(" ", sanitized)
    sanitized = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", sanitized).strip()

    invalid_labels = [
        label for label in _CITATION_LABEL.findall(answer) if not 1 <= int(label) <= available
    ]
    if invalid_labels:
        logger.warning(
            "Discarded fabricated or out-of-range citations",
            extra={"labels": sorted(set(invalid_labels)), "available": available},
        )

    return sanitized, [_citation(hits[index]) for index in cited_indexes]


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

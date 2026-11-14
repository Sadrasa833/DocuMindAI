from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.conversation import Conversation
from app.repositories.conversations import ConversationRepository


@dataclass(slots=True)
class ConversationListResult:
    items: list[tuple[Conversation, int]]
    total: int


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)

    async def create(self, user_id: uuid.UUID, title: str | None) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title or "New conversation")
        await self.conversations.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def list(
        self, user_id: uuid.UUID, *, page: int, page_size: int
    ) -> ConversationListResult:
        items, total = await self.conversations.list_owned(user_id, page=page, page_size=page_size)
        return ConversationListResult(items=items, total=total)

    async def get_owned(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
        conversation = await self.conversations.get_owned(
            conversation_id, user_id, with_messages=True
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    async def delete(self, conversation_id: uuid.UUID, user_id: uuid.UUID) -> None:
        conversation = await self.conversations.get_owned(conversation_id, user_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        await self.conversations.delete(conversation)
        await self.session.commit()

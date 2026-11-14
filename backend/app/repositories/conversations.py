from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation
from app.models.message import Message


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, conversation: Conversation) -> Conversation:
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def add_message(self, message: Message) -> Message:
        self.session.add(message)
        conversation = await self.session.get(Conversation, message.conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(UTC)
        await self.session.flush()
        return message

    async def get_owned(
        self,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        with_messages: bool = False,
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if with_messages:
            stmt = stmt.options(selectinload(Conversation.messages))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def recent_messages(self, conversation_id: uuid.UUID, *, limit: int) -> list[Message]:
        if limit <= 0:
            return []
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def count_messages(self, conversation_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
        return int(result.scalar_one())

    async def list_owned(
        self, user_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[Conversation, int]], int]:
        count_result = await self.session.execute(
            select(func.count(Conversation.id)).where(Conversation.user_id == user_id)
        )
        total = int(count_result.scalar_one())
        message_count = (
            select(func.count(Message.id))
            .where(Message.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        result = await self.session.execute(
            select(Conversation, message_count)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [(row[0], int(row[1])) for row in result.all()], total

    async def delete(self, conversation: Conversation) -> None:
        await self.session.delete(conversation)

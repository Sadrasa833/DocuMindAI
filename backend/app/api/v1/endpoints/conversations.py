from __future__ import annotations

import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, get_conversation_service
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    MessageResponse,
)
from app.services.conversations import ConversationService

router = APIRouter(prefix="/conversations", tags=["Conversations"])


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: CurrentUser,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationSummary:
    conversation = await service.create(current_user.id, payload.title)
    return ConversationSummary.model_validate(conversation)


@router.get("", response_model=PaginatedResponse[ConversationSummary])
async def list_conversations(
    current_user: CurrentUser,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedResponse[ConversationSummary]:
    result = await service.list(current_user.id, page=page, page_size=page_size)
    return PaginatedResponse[ConversationSummary](
        items=[
            ConversationSummary(
                id=conversation.id,
                title=conversation.title,
                message_count=message_count,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
            for conversation, message_count in result.items
        ],
        total=result.total,
        page=page,
        page_size=page_size,
        pages=math.ceil(result.total / page_size) if result.total else 0,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ConversationDetail:
    conversation = await service.get_owned(conversation_id, current_user.id)
    messages = [MessageResponse.model_validate(message) for message in conversation.messages]
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        message_count=len(messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=messages,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: CurrentUser,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> Response:
    await service.delete(conversation_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

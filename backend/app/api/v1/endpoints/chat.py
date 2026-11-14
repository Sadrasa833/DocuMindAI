from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, get_rag_service
from app.core.rate_limit import rate_limit
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.conversation import Citation
from app.services.rag import RAGService

router = APIRouter(tags=["Chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(rate_limit("chat", "chat_rate_limit_per_minute"))],
)
async def chat(
    payload: ChatRequest,
    current_user: CurrentUser,
    service: Annotated[RAGService, Depends(get_rag_service)],
) -> ChatResponse:
    result = await service.answer(
        user_id=current_user.id,
        question=payload.question,
        document_ids=payload.document_ids,
        conversation_id=payload.conversation_id,
    )
    return ChatResponse(
        answer=result.answer,
        conversation_id=result.conversation_id,
        citations=[Citation.model_validate(citation) for citation in result.citations],
    )

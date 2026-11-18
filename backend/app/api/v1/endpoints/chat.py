from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, get_rag_service
from app.core.rate_limit import rate_limit
from app.schemas.chat import ChatRequest, ChatResponse, CitationValidationSummary
from app.schemas.conversation import Citation
from app.services.rag import STREAM_FAILED_MESSAGE, PreparedStream, RAGService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


def _sse_event(event: dict[str, Any]) -> str:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event['type']}\ndata: {payload}\n\n"


async def _stream_response(
    service: RAGService,
    prepared: PreparedStream,
    request: Request,
) -> AsyncIterator[str]:
    try:
        async with aclosing(service.stream_events(prepared)) as events:
            async for event in events:
                if await request.is_disconnected():
                    return
                yield _sse_event(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Unhandled chat streaming error")
        yield _sse_event({"type": "error", "message": STREAM_FAILED_MESSAGE})


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
        citation_validation=(
            CitationValidationSummary.model_validate(result.citation_validation)
            if result.citation_validation is not None
            else None
        ),
    )


@router.post(
    "/chat/stream",
    dependencies=[Depends(rate_limit("chat", "chat_rate_limit_per_minute"))],
)
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    current_user: CurrentUser,
    service: Annotated[RAGService, Depends(get_rag_service)],
) -> StreamingResponse:
    prepared = await service.prepare_stream(
        user_id=current_user.id,
        question=payload.question,
        document_ids=payload.document_ids,
        conversation_id=payload.conversation_id,
    )
    return StreamingResponse(
        _stream_response(service, prepared, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )

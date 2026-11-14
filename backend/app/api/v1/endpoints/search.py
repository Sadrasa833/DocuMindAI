from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, get_search_service
from app.core.rate_limit import rate_limit
from app.schemas.search import SearchRequest, SearchResponse, SearchResult
from app.services.search import SearchService

router = APIRouter(tags=["Search"])


@router.post(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(rate_limit("search", "search_rate_limit_per_minute"))],
)
async def semantic_search(
    payload: SearchRequest,
    current_user: CurrentUser,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchResponse:
    hits = await service.search(
        user_id=current_user.id,
        query=payload.query,
        document_ids=payload.document_ids,
        top_k=payload.top_k,
    )
    return SearchResponse(
        query=payload.query,
        results=[
            SearchResult(
                chunk_id=hit.chunk.id,
                document_id=hit.chunk.document_id,
                document_name=hit.document_name,
                page_number=hit.chunk.page_number,
                chunk_index=hit.chunk.chunk_index,
                content=hit.chunk.content,
                similarity=round(hit.similarity, 4),
            )
            for hit in hits
        ],
    )

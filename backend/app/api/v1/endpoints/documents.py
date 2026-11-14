from __future__ import annotations

import math
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from app.api.deps import (
    CurrentUser,
    get_document_service,
    get_summarization_service,
)
from app.core.rate_limit import rate_limit
from app.models.enums import DocumentStatus
from app.schemas.common import PaginatedResponse
from app.schemas.document import (
    DocumentResponse,
    DocumentStatsResponse,
    DocumentStatusResponse,
    SummarizeRequest,
    SummaryResponse,
)
from app.services.documents import DocumentService
from app.services.summarization import SummarizationService

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("documents-upload", "upload_rate_limit_per_minute"))],
)
async def upload_document(
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(description="PDF file to process")],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    document = await service.upload(user_id=current_user.id, file=file)
    return DocumentResponse.model_validate(document)


@router.get("", response_model=PaginatedResponse[DocumentResponse])
async def list_documents(
    current_user: CurrentUser,
    service: Annotated[DocumentService, Depends(get_document_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    document_status: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    sort_by: Annotated[
        Literal["created_at", "original_filename", "file_size"], Query()
    ] = "created_at",
    sort_order: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> PaginatedResponse[DocumentResponse]:
    documents, total = await service.list(
        current_user.id,
        page=page,
        page_size=page_size,
        status=document_status,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return PaginatedResponse[DocumentResponse](
        items=[DocumentResponse.model_validate(document) for document in documents],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/stats", response_model=DocumentStatsResponse)
async def document_stats(
    current_user: CurrentUser,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentStatsResponse:
    return DocumentStatsResponse(**await service.stats(current_user.id))


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    document = await service.get(document_id, current_user.id)
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentStatusResponse:
    document = await service.status(document_id, current_user.id)
    return DocumentStatusResponse.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUser,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> Response:
    await service.delete(document_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/summarize",
    response_model=SummaryResponse,
    dependencies=[Depends(rate_limit("documents-summarize", "chat_rate_limit_per_minute"))],
)
async def summarize_document(
    document_id: uuid.UUID,
    payload: SummarizeRequest,
    current_user: CurrentUser,
    service: Annotated[SummarizationService, Depends(get_summarization_service)],
) -> SummaryResponse:
    result = await service.summarize(
        user_id=current_user.id,
        document_id=document_id,
        style=payload.style,
        force=payload.force,
    )
    return SummaryResponse(
        document_id=result.document_id,
        style=result.style,
        summary=result.summary,
        created_at=result.created_at,
    )

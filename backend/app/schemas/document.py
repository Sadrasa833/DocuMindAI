from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentStatus, SummaryStyle


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    mime_type: str
    file_size: int
    status: DocumentStatus
    page_count: int | None
    chunk_count: int
    error_message: str | None
    summary: str | None
    summary_style: SummaryStyle | None
    summarized_at: datetime | None
    processed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: DocumentStatus
    error_message: str | None
    page_count: int | None
    chunk_count: int
    updated_at: datetime


class DocumentStatsResponse(BaseModel):
    total_documents: int
    completed_documents: int
    processing_documents: int
    failed_documents: int
    total_chunks: int
    total_pages: int


class SummarizeRequest(BaseModel):
    style: SummaryStyle = SummaryStyle.SHORT
    force: bool = Field(
        default=False,
        description="Regenerate the summary even when a summary in this style already exists.",
    )


class SummaryResponse(BaseModel):
    document_id: uuid.UUID
    style: SummaryStyle
    summary: str
    created_at: datetime

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from app.schemas.conversation import Citation


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    conversation_id: uuid.UUID | None = None

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Question must contain at least 2 non-whitespace characters")
        return normalized


class ChatResponse(BaseModel):
    answer: str
    conversation_id: uuid.UUID
    citations: list[Citation]

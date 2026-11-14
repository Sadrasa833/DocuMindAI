from __future__ import annotations

import hashlib
import io
import re
import textwrap
import uuid
from collections.abc import Sequence
from typing import Any

from httpx import AsyncClient
from reportlab.pdfgen import canvas

from app.services.embeddings import EmbeddingService

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class FakeTaskDispatcher:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue_document_processing(self, document_id: uuid.UUID) -> None:
        self.enqueued.append(document_id)


class FakeEmbeddingProvider:
    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _TOKEN_PATTERN.findall(text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.dimensions
            vector[index] += 1.0
            for offset in (1, 2):
                source = f"{token}:{offset}"
                digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
                neighbor = int(digest[:8], 16) % self.dimensions
                vector[neighbor] += 0.25
        if not any(vector):
            vector[0] = 1.0
        return vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]


class FakeChatProvider:
    def __init__(self) -> None:
        self.response = "The documents state that payment is due within 30 days [S1]."
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return self.response


def make_embedding_service(provider: FakeEmbeddingProvider) -> EmbeddingService:
    return EmbeddingService(provider, dimensions=provider.dimensions, batch_size=2)


def make_pdf(pages: list[str]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    for text in pages:
        text_object = pdf.beginText(72, 720)
        text_object.setFont("Helvetica", 11)
        for line in textwrap.wrap(text, width=85):
            text_object.textLine(line)
        pdf.drawText(text_object)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


DEFAULT_PAGES = [
    (
        "Acme Services Agreement. Payment terms are net thirty days from invoice date. "
        "Late payments accrue interest at one percent per month. The customer must pay all "
        "undisputed invoices within the payment window."
    ),
    (
        "Confidentiality and general provisions. Each party must protect confidential "
        "information. The agreement renews automatically for one year unless either party "
        "provides sixty days written notice."
    ),
]


async def register_user(
    client: AsyncClient,
    *,
    email: str = "ada@example.com",
    password: str = "SecurePass123",
    full_name: str = "Ada Lovelace",
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


async def upload_pdf(
    client: AsyncClient,
    pages: list[str] | None = None,
    *,
    filename: str = "agreement.pdf",
    content_type: str = "application/pdf",
) -> Any:
    return await client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, make_pdf(pages or DEFAULT_PAGES), content_type)},
    )

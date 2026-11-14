from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

from app.ai.base import EmbeddingProvider
from app.core.exceptions import AIProviderError


class EmbeddingService:
    """Batches, de-duplicates and validates embeddings from the configured provider."""

    def __init__(
        self, provider: EmbeddingProvider, *, dimensions: int, batch_size: int = 64
    ) -> None:
        self.provider = provider
        self.dimensions = dimensions
        self.batch_size = batch_size

    def _validate_dimensions(self, embeddings: list[list[float]], expected_count: int) -> None:
        if len(embeddings) != expected_count:
            raise AIProviderError("The embedding provider returned an unexpected number of vectors")
        for embedding in embeddings:
            if len(embedding) != self.dimensions:
                raise AIProviderError(
                    "The embedding provider returned vectors with an unexpected dimension; "
                    "check EMBEDDING_DIMENSIONS and OPENAI_EMBEDDING_MODEL"
                )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        unique_texts = list(OrderedDict.fromkeys(texts))
        embedded: list[list[float]] = []
        for start in range(0, len(unique_texts), self.batch_size):
            batch = unique_texts[start : start + self.batch_size]
            vectors = await self.provider.embed(batch)
            self._validate_dimensions(vectors, len(batch))
            embedded.extend(vectors)
        by_text = dict(zip(unique_texts, embedded, strict=True))
        return [by_text[text] for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.provider.embed([text])
        self._validate_dimensions(vectors, 1)
        return vectors[0]

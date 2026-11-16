from __future__ import annotations

import pytest

from app.ai.openai_provider import OpenAIChatProvider, OpenAIEmbeddingProvider
from app.core.config import get_settings


class _ClosableClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("provider_class", [OpenAIEmbeddingProvider, OpenAIChatProvider])
async def test_openai_providers_close_their_http_client(
    provider_class: type[OpenAIEmbeddingProvider | OpenAIChatProvider],
) -> None:
    provider = provider_class(get_settings())
    client = _ClosableClient()
    provider._client = client  # type: ignore[assignment]

    await provider.aclose()
    await provider.aclose()

    assert client.closed
    assert provider._client is None

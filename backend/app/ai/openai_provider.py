from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

import openai
from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings
from app.core.exceptions import AIProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.settings.openai_key,
                base_url=self.settings.openai_base_url,
                timeout=self.settings.openai_timeout_seconds,
                max_retries=0,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        request_kwargs: dict[str, object] = {
            "model": self.settings.openai_embedding_model,
            "input": list(texts),
        }
        if self.settings.openai_embedding_model.startswith("text-embedding-3"):
            request_kwargs["dimensions"] = self.settings.embedding_dimensions
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type(_RETRYABLE_ERRORS),
                reraise=True,
            ):
                with attempt:
                    response = await self._get_client().embeddings.create(**request_kwargs)
                    ordered = sorted(response.data, key=lambda item: item.index)
                    return [list(item.embedding) for item in ordered]
        except _RETRYABLE_ERRORS as exc:
            logger.warning("OpenAI embedding request failed after retries: %s", type(exc).__name__)
            raise AIProviderError("The embedding service is temporarily unavailable") from exc
        except openai.OpenAIError as exc:
            logger.error("OpenAI embedding request rejected: %s", type(exc).__name__)
            raise AIProviderError("The embedding service could not process this request") from exc
        return []


class OpenAIChatProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.settings.openai_key,
                base_url=self.settings.openai_base_url,
                timeout=self.settings.openai_timeout_seconds,
                max_retries=0,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> str:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type(_RETRYABLE_ERRORS),
                reraise=True,
            ):
                with attempt:
                    response = await self._get_client().chat.completions.create(
                        model=self.settings.openai_chat_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    content = response.choices[0].message.content if response.choices else None
                    if not content or not content.strip():
                        raise AIProviderError("The AI model returned an empty response")
                    return content.strip()
        except AIProviderError:
            raise
        except _RETRYABLE_ERRORS as exc:
            logger.warning("OpenAI chat request failed after retries: %s", type(exc).__name__)
            raise AIProviderError("The AI service is temporarily unavailable") from exc
        except openai.OpenAIError as exc:
            logger.error("OpenAI chat request rejected: %s", type(exc).__name__)
            raise AIProviderError("The AI service could not process this request") from exc
        raise AIProviderError("The AI service did not return a response")

    async def stream(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> AsyncIterator[str]:
        emitted = False
        last_error: Exception | None = None
        for attempt in range(3):
            response = None
            try:
                response = await self._get_client().chat.completions.create(
                    model=self.settings.openai_chat_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
                async for event in response:
                    if not event.choices:
                        continue
                    delta = event.choices[0].delta.content
                    if delta:
                        emitted = True
                        yield delta
                if not emitted:
                    raise AIProviderError("The AI model returned an empty response")
                return
            except AIProviderError:
                raise
            except _RETRYABLE_ERRORS as exc:
                last_error = exc
                if emitted:
                    break
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
            except openai.OpenAIError as exc:
                logger.error("OpenAI streaming request rejected: %s", type(exc).__name__)
                raise AIProviderError("The AI service could not process this request") from exc
            finally:
                if response is not None:
                    close = getattr(response, "close", None)
                    if close is not None:
                        try:
                            await close()
                        except Exception:
                            logger.debug("Failed to close OpenAI stream", exc_info=True)
        if last_error is not None:
            logger.warning(
                "OpenAI streaming request failed after retries: %s",
                type(last_error).__name__,
            )
        raise AIProviderError("The AI service is temporarily unavailable") from last_error

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from fastapi import Request

from app.core.config import Settings, get_settings
from app.core.exceptions import RateLimitExceededError

logger = logging.getLogger(__name__)


class RateLimiter:
    """Redis-backed fixed-window limiter with an in-process fallback for availability."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._redis: redis.Redis | None = None
        self._redis_failed = False
        self._redis_retry_at = 0.0
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    def _client(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(
                self.settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
        return self._redis

    async def _check_redis(self, key: str, limit: int, window: int) -> bool | None:
        now = time.monotonic()
        if self._redis_failed and now < self._redis_retry_at:
            return None
        try:
            client = self._client()
            async with client.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window, nx=True)
                count, _ = await pipeline.execute()
            self._redis_failed = False
            return int(count) <= limit
        except Exception as exc:
            self._redis_failed = True
            self._redis_retry_at = now + 30
            logger.warning("Redis rate limiter unavailable; using in-memory fallback: %s", exc)
            return None

    async def _check_memory(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            expired = [k for k, (_, expiry) in self._memory.items() if expiry <= now]
            for expired_key in expired:
                self._memory.pop(expired_key, None)
            count, expiry = self._memory.get(key, (0, now + window))
            if expiry <= now:
                count, expiry = 0, now + window
            count += 1
            self._memory[key] = (count, expiry)
            return count <= limit

    async def check(self, key: str, *, limit: int, window: int = 60) -> bool:
        redis_result = await self._check_redis(f"rate:{key}:{window}", limit, window)
        if redis_result is not None:
            return redis_result
        return await self._check_memory(key, limit, window)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings())
    return _limiter


def rate_limit(scope: str, limit_attr: str) -> Callable[[Request], Awaitable[None]]:
    async def dependency(request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit = int(getattr(settings, limit_attr))
        client_host = request.client.host if request.client else "unknown"
        key = f"{scope}:{client_host}"
        if not await get_rate_limiter().check(key, limit=limit, window=60):
            raise RateLimitExceededError(
                "Too many requests. Please wait a moment before trying again."
            )

    return dependency

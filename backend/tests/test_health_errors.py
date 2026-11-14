from __future__ import annotations

import asyncio

from httpx import AsyncClient

from app.core.rate_limit import RateLimiter


async def test_health_and_root_endpoints(client: AsyncClient) -> None:
    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert live.json()["version"] == "1.0.0"

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"database": True}}

    root = await client.get("/")
    assert root.status_code == 200
    assert root.json()["docs"] == "/docs"


async def test_request_id_and_security_headers(client: AsyncClient) -> None:
    supplied = await client.get("/health/live", headers={"X-Request-ID": "client-request-123"})
    assert supplied.headers["X-Request-ID"] == "client-request-123"
    assert supplied.headers["X-Content-Type-Options"] == "nosniff"
    assert supplied.headers["X-Frame-Options"] == "DENY"
    assert "Permissions-Policy" in supplied.headers

    generated = await client.get(
        "/health/live", headers={"X-Request-ID": "invalid request id with spaces"}
    )
    assert generated.headers["X-Request-ID"]
    assert generated.headers["X-Request-ID"] != "invalid request id with spaces"
    api_response = await client.get("/api/v1/auth/me")
    assert api_response.headers["Cache-Control"] == "no-store"


async def test_error_envelope_for_unauthenticated_and_missing_routes(
    client: AsyncClient,
) -> None:
    unauthorized = await client.get("/api/v1/documents")
    assert unauthorized.status_code == 401
    body = unauthorized.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"]
    assert body["error"]["request_id"] == unauthorized.headers["X-Request-ID"]

    missing = await client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "http_error"
    assert missing.json()["error"]["request_id"]

    method = await client.delete("/health/live")
    assert method.status_code == 405
    assert method.json()["error"]["code"] == "http_error"


async def test_validation_errors_include_field_details(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "bad", "password": "short", "full_name": "A"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    fields = {detail["field"] for detail in error["details"]}
    assert "body.email" in fields
    assert "body.password" in fields
    assert "body.full_name" in fields


async def test_cors_preflight(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://testserver",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://testserver"
    assert "access-control-allow-credentials" in response.headers


async def test_rate_limit_returns_structured_429(
    client: AsyncClient, monkeypatch, test_settings
) -> None:
    from app.core import rate_limit as rate_limit_module

    class DenyLimiter:
        async def check(self, key: str, *, limit: int, window: int = 60) -> bool:
            return False

    monkeypatch.setattr(rate_limit_module, "_limiter", DenyLimiter())
    monkeypatch.setattr(test_settings, "rate_limit_enabled", True)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "SecurePass123"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


async def test_openapi_lists_core_routes(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    expected = {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/documents/upload",
        "/api/v1/documents/{document_id}/summarize",
        "/api/v1/search",
        "/api/v1/chat",
        "/api/v1/conversations",
        "/health/live",
        "/health/ready",
    }
    assert expected <= set(paths)
    assert response.json()["info"]["title"] == "DocuMind AI API"


async def test_in_memory_rate_limiter_fallback() -> None:
    class FakeSettings:
        redis_url = "redis://127.0.0.1:1/0"

    limiter = RateLimiter(FakeSettings())  # type: ignore[arg-type]
    limiter._redis_failed = True
    limiter._redis_retry_at = float("inf")
    assert await limiter.check("scope:client", limit=2, window=60) is True
    assert await limiter.check("scope:client", limit=2, window=60) is True
    assert await limiter.check("scope:client", limit=2, window=60) is False
    await asyncio.sleep(0)

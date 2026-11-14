from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AIProviderError, AppError
from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request_id_ctx.get()


def _error_payload(
    *,
    code: str,
    message: str,
    request_id: str | None,
    details: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "error": {"code": code, "message": message, "request_id": request_id}
    }
    if details:
        body["error"]["details"] = details  # type: ignore[index]
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if exc.status_code == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                code=exc.code, message=exc.message, request_id=_request_id(request)
            ),
            headers=headers,
        )

    @app.exception_handler(AIProviderError)
    async def ai_error_handler(request: Request, exc: AIProviderError) -> JSONResponse:
        logger.warning("AI provider failure: %s", exc.message)
        return JSONResponse(
            status_code=503,
            content=_error_payload(
                code="ai_service_unavailable",
                message=exc.message,
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error.get("loc", [])),
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                code="validation_error",
                message="The request contains invalid data",
                request_id=_request_id(request),
                details=details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                code="http_error",
                message=detail,
                request_id=_request_id(request),
            ),
            headers=exc.headers or {},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled application exception",
            extra={"method": request.method, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                code="internal_server_error",
                message="An unexpected error occurred. Please try again.",
                request_id=_request_id(request),
            ),
        )

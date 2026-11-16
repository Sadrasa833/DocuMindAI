from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.v1.api import api_router
from app.api.v1.endpoints.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.db.session import engine

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {
        "name": "Authentication",
        "description": "Registration, login, refresh and profile management.",
    },
    {"name": "Documents", "description": "PDF upload, ownership-scoped management and summaries."},
    {"name": "Search", "description": "Semantic search across a user's processed documents."},
    {"name": "Chat", "description": "Grounded RAG answers with citations and persisted history."},
    {"name": "Conversations", "description": "Conversation and message history management."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting application",
        extra={
            "environment": settings.environment,
            "version": settings.app_version,
        },
    )
    yield
    from app.api.deps import get_chat_provider, get_embedding_provider

    for provider in (get_embedding_provider(), get_chat_provider()):
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    await engine.dispose()
    logger.info("Application stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        description=(
            "DocuMind AI is a multi-user document intelligence platform with asynchronous "
            "PDF processing, pgvector semantic search, citation-grounded RAG answers, and "
            "document summaries. Authentication uses short-lived JWT access cookies and "
            "rotating refresh cookies. Bearer access tokens are also accepted by protected APIs."
        ),
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router, prefix=settings.api_v1_prefix)

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    return application


app = create_app()

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import check_database

router = APIRouter(tags=["Health"])


@router.get("/health/live", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
    }


@router.get("/health/ready", summary="Readiness probe")
async def readiness() -> JSONResponse:
    database_ok = await check_database()
    payload = {
        "status": "ready" if database_ok else "not_ready",
        "checks": {"database": database_ok},
    }
    return JSONResponse(status_code=200 if database_ok else 503, content=payload)

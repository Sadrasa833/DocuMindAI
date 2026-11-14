from __future__ import annotations

from fastapi import Response

from app.core.config import Settings
from app.services.auth import AuthTokens


def set_auth_cookies(response: Response, tokens: AuthTokens, settings: Settings) -> None:
    access_max_age = settings.access_token_expire_minutes * 60
    refresh_max_age = settings.refresh_token_expire_days * 24 * 60 * 60
    common = {
        "path": "/",
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        key=settings.access_cookie_name,
        value=tokens.access_token,
        max_age=access_max_age,
        **common,
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=tokens.refresh_token,
        max_age=refresh_max_age,
        **common,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    common = {
        "path": "/",
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }
    response.delete_cookie(settings.access_cookie_name, **common)
    response.delete_cookie(settings.refresh_cookie_name, **common)

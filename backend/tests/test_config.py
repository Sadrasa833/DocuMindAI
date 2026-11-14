from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE_SETTINGS = {
    "environment": "test",
    "secret_key": "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "http://testserver",
}


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**BASE_SETTINGS, **overrides})


def test_empty_optional_values_are_normalized() -> None:
    settings = make_settings(openai_base_url="", cookie_domain="")

    assert settings.openai_base_url is None
    assert settings.cookie_domain is None


def test_wildcard_cors_origins_are_rejected() -> None:
    with pytest.raises(ValidationError, match="wildcards are not allowed"):
        make_settings(cors_origins="https://example.com,*")


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE must be true"):
        make_settings(environment="production", cookie_secure=False)


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        make_settings(environment="production", cookie_secure=True, debug=True)

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


def test_ocr_defaults_target_english_and_persian() -> None:
    settings = make_settings()
    assert settings.ocr_enabled is True
    assert settings.ocr_languages == "eng+fas"
    assert settings.ocr_dpi == 200
    assert settings.ocr_max_pages == 50


def test_ocr_language_codes_are_validated() -> None:
    with pytest.raises(ValidationError, match="Tesseract language codes"):
        make_settings(ocr_languages="eng; rm -rf /")


def test_ocr_page_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_settings(ocr_max_pages=0)

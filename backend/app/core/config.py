from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env files."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", PROJECT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "DocuMind AI"
    app_version: str = "1.0.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = True
    api_v1_prefix: str = "/api/v1"

    secret_key: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "documind-ai"
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    refresh_reuse_grace_seconds: int = Field(default=30, ge=0, le=300)
    access_cookie_name: str = "documind_access"
    refresh_cookie_name: str = "documind_refresh"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    database_url: str = "postgresql+psycopg://documind:documind@localhost:5432/documind"
    database_echo: bool = False
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=100)

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    openai_api_key: SecretStr
    openai_base_url: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=8192)
    embedding_batch_size: int = Field(default=64, ge=1, le=512)
    openai_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    max_upload_size_mb: int = Field(default=20, ge=1, le=100)
    storage_path: str = "data/uploads"
    processing_stuck_after_seconds: int = Field(default=1800, ge=900)
    processing_recovery_interval_seconds: int = Field(default=300, ge=30)
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=200, ge=0, le=2000)
    ocr_enabled: bool = True
    ocr_languages: str = "eng+fas"
    ocr_dpi: int = Field(default=200, ge=100, le=400)
    ocr_max_pages: int = Field(default=50, ge=1, le=500)
    ocr_timeout_seconds: int = Field(default=30, ge=5, le=300)
    search_default_top_k: int = Field(default=5, ge=1, le=20)
    search_min_similarity: float = Field(default=0.2, ge=0.0, le=0.95)
    rag_max_context_chunks: int = Field(default=8, ge=1, le=20)
    rag_history_messages: int = Field(default=8, ge=0, le=30)
    summary_map_chunk_chars: int = Field(default=12_000, ge=1000, le=50_000)
    summary_reduce_chunk_chars: int = Field(default=20_000, ge=1000, le=80_000)
    summary_max_tokens: int = Field(default=1200, ge=100, le=4000)

    cors_origins: str = "http://localhost:3000"
    rate_limit_enabled: bool = True
    chat_rate_limit_per_minute: int = Field(default=20, ge=1)
    auth_rate_limit_per_minute: int = Field(default=10, ge=1)
    search_rate_limit_per_minute: int = Field(default=60, ge=1)
    upload_rate_limit_per_minute: int = Field(default=10, ge=1)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def storage_directory(self) -> Path:
        path = Path(self.storage_path)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def openai_key(self) -> str:
        return self.openai_api_key.get_secret_value()

    @field_validator("ocr_languages")
    @classmethod
    def validate_ocr_languages(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_+-]+", normalized):
            raise ValueError("OCR_LANGUAGES must contain Tesseract language codes separated by '+'")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def normalize_optional_urls(cls, values: object) -> object:
        if isinstance(values, dict) and values.get("openai_base_url") == "":
            values["openai_base_url"] = None
        if isinstance(values, dict) and values.get("cookie_domain") == "":
            values["cookie_domain"] = None
        return values

    @model_validator(mode="after")
    def validate_security_and_processing(self) -> Settings:
        secret = self.secret_key.get_secret_value()
        if len(secret) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 characters")
        if self.environment == "production" and "change-me" in secret.lower():
            raise ValueError("Change the development SECRET_KEY before running in production")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if not self.openai_key.strip():
            raise ValueError("OPENAI_API_KEY is required")
        if "*" in self.cors_origins_list:
            raise ValueError("CORS_ORIGINS must list explicit origins; wildcards are not allowed")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when COOKIE_SAMESITE=none")
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if self.environment == "production" and self.debug:
            raise ValueError("DEBUG must be false in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

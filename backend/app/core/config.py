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

    frontend_base_url: str = "http://localhost:3000"
    email_backend: Literal["console", "smtp"] = "console"
    email_from: str = "DocuMind AI <no-reply@documind.local>"
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    email_verification_required: bool = False
    email_verification_token_hours: int = Field(default=24, ge=1, le=168)
    password_reset_token_minutes: int = Field(default=60, ge=5, le=1440)
    token_cleanup_retention_days: int = Field(default=30, ge=1, le=365)
    token_cleanup_interval_seconds: int = Field(default=86400, ge=300)

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
    user_storage_quota_mb: int = Field(default=1024, ge=1, le=1_000_000)
    storage_backend: Literal["local", "s3"] = "local"
    storage_path: str = "data/uploads"
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_use_ssl: bool = True
    malware_scanning_enabled: bool = False
    clamav_host: str = "localhost"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
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
    rag_citation_validation_enabled: bool = True
    rag_citation_min_support: float = Field(default=0.55, ge=0.0, le=1.0)
    rag_citation_require_citations: bool = True
    rag_citation_semantic_check: bool = False
    rag_citation_semantic_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    summary_map_chunk_chars: int = Field(default=12_000, ge=1000, le=50_000)
    summary_reduce_chunk_chars: int = Field(default=20_000, ge=1000, le=80_000)
    summary_max_tokens: int = Field(default=1200, ge=100, le=4000)
    summary_stuck_after_seconds: int = Field(default=1800, ge=300)
    summary_recovery_interval_seconds: int = Field(default=300, ge=30)

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
    def user_storage_quota_bytes(self) -> int:
        return self.user_storage_quota_mb * 1024 * 1024

    @property
    def storage_directory(self) -> Path:
        path = Path(self.storage_path)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def s3_secret_key(self) -> str | None:
        return self.s3_secret_access_key.get_secret_value() if self.s3_secret_access_key else None

    @property
    def smtp_password_value(self) -> str | None:
        return self.smtp_password.get_secret_value() if self.smtp_password else None

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
        if isinstance(values, dict):
            for key in (
                "openai_base_url",
                "cookie_domain",
                "s3_bucket",
                "s3_endpoint_url",
                "s3_access_key_id",
                "s3_secret_access_key",
                "smtp_host",
                "smtp_username",
                "smtp_password",
            ):
                if values.get(key) == "":
                    values[key] = None
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
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        if bool(self.s3_access_key_id) != bool(self.s3_secret_access_key):
            raise ValueError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together")
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        if self.environment == "production" and self.email_backend == "console":
            raise ValueError("EMAIL_BACKEND=console is not allowed in production")
        if self.email_backend == "smtp" and bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be set together")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

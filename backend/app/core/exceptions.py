from __future__ import annotations

import uuid
from typing import Any


class AppError(Exception):
    """Base class for expected application errors mapped to HTTP responses."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class StorageQuotaExceededError(AppError):
    status_code = 413
    code = "storage_quota_exceeded"


class MalwareDetectedError(AppError):
    status_code = 400
    code = "malware_detected"


class MalwareScannerUnavailableError(AppError):
    status_code = 503
    code = "malware_scanner_unavailable"


class EmailVerificationRequiredError(AppError):
    status_code = 403
    code = "email_verification_required"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class RateLimitExceededError(AppError):
    status_code = 429
    code = "rate_limit_exceeded"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


class AIProviderError(Exception):
    """Raised by the AI integration layer when an upstream call cannot complete."""

    def __init__(self, message: str = "The AI service is temporarily unavailable") -> None:
        super().__init__(message)
        self.message = message


class DocumentProcessingError(Exception):
    """An expected failure while processing a document."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        attempt_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.attempt_id = attempt_id


class ValidationErrorDetail(dict[str, Any]):
    pass

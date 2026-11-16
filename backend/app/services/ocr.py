from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class OcrError(Exception):
    """Base error for OCR engine failures."""


class OcrUnavailableError(OcrError):
    """Raised when the OCR engine is not installed or cannot be started."""


class OcrEngine(Protocol):
    def image_to_text(self, image: Any) -> str: ...


@dataclass(frozen=True, slots=True)
class OcrOptions:
    enabled: bool = True
    languages: str = "eng+fas"
    dpi: int = 200
    max_pages: int = 50
    timeout_seconds: int = 30


class TesseractOcrEngine:
    def __init__(self, *, languages: str, timeout_seconds: int) -> None:
        self.languages = languages
        self.timeout_seconds = timeout_seconds

    def image_to_text(self, image: Any) -> str:
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrUnavailableError(
                "OCR is required for this PDF but the OCR engine is not installed"
            ) from exc

        try:
            return pytesseract.image_to_string(
                image,
                lang=self.languages,
                timeout=self.timeout_seconds,
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailableError(
                "OCR is required for this PDF but the OCR engine is not available"
            ) from exc

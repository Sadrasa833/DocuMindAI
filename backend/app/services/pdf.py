from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import DocumentProcessingError
from app.utils.text import clean_page_text


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(slots=True)
class PdfExtraction:
    pages: list[ExtractedPage]
    page_count: int


def extract_pdf_text(data: bytes) -> PdfExtraction:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise DocumentProcessingError(
                        "Password-protected PDFs are not supported", retryable=False
                    )
            except Exception as exc:
                if isinstance(exc, DocumentProcessingError):
                    raise
                raise DocumentProcessingError(
                    "Password-protected PDFs are not supported", retryable=False
                ) from exc

        pages: list[ExtractedPage] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                raw_text = page.extract_text() or ""
            except Exception as exc:
                raise DocumentProcessingError(
                    f"Could not extract text from page {index}", retryable=False
                ) from exc
            cleaned = clean_page_text(raw_text)
            if cleaned:
                pages.append(ExtractedPage(page_number=index, text=cleaned))

        if not pages:
            raise DocumentProcessingError(
                "No selectable text was found in this PDF. Scanned documents require OCR.",
                retryable=False,
            )
        return PdfExtraction(pages=pages, page_count=len(reader.pages))
    except DocumentProcessingError:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise DocumentProcessingError("The PDF could not be read", retryable=False) from exc

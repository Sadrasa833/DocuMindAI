from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import DocumentProcessingError
from app.services.ocr import (
    OcrEngine,
    OcrOptions,
    OcrUnavailableError,
    TesseractOcrEngine,
)
from app.utils.text import clean_page_text

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(slots=True)
class PdfExtraction:
    pages: list[ExtractedPage]
    page_count: int


def _render_pages(data: bytes, page_numbers: list[int], dpi: int) -> Iterator[tuple[int, Any]]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise DocumentProcessingError(
            "OCR is required for this PDF but the OCR renderer is not installed",
            retryable=False,
        ) from exc

    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise DocumentProcessingError(
            "The PDF could not be prepared for OCR", retryable=False
        ) from exc

    try:
        for page_number in page_numbers:
            bitmap = None
            try:
                page = document[page_number - 1]
                bitmap = page.render(scale=dpi / 72)
                yield page_number, bitmap.to_pil()
            except DocumentProcessingError:
                raise
            except Exception as exc:
                raise DocumentProcessingError(
                    f"Could not render page {page_number} for OCR", retryable=False
                ) from exc
            finally:
                if bitmap is not None:
                    bitmap.close()
    finally:
        document.close()


def _ocr_pages(
    data: bytes,
    page_numbers: list[int],
    options: OcrOptions,
    engine: OcrEngine | None,
) -> dict[int, str]:
    if len(page_numbers) > options.max_pages:
        raise DocumentProcessingError(
            f"OCR is required for {len(page_numbers)} pages, which exceeds the "
            f"configured limit of {options.max_pages} pages",
            retryable=False,
        )

    selected_engine = engine or TesseractOcrEngine(
        languages=options.languages,
        timeout_seconds=options.timeout_seconds,
    )
    logger.info(
        "Running OCR for pages without selectable text",
        extra={
            "pages": len(page_numbers),
            "languages": options.languages,
            "dpi": options.dpi,
        },
    )

    texts: dict[int, str] = {}
    for page_number, image in _render_pages(data, page_numbers, options.dpi):
        try:
            raw_text = selected_engine.image_to_text(image)
        except OcrUnavailableError as exc:
            raise DocumentProcessingError(str(exc), retryable=False) from exc
        except DocumentProcessingError:
            raise
        except Exception as exc:
            raise DocumentProcessingError(
                f"OCR failed on page {page_number}", retryable=True
            ) from exc
        cleaned = clean_page_text(raw_text or "")
        if cleaned:
            texts[page_number] = cleaned
    return texts


def extract_pdf_text(
    data: bytes,
    *,
    ocr_options: OcrOptions | None = None,
    ocr_engine: OcrEngine | None = None,
) -> PdfExtraction:
    options = ocr_options or OcrOptions()
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

        page_texts: dict[int, str] = {}
        pages_without_text: list[int] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                raw_text = page.extract_text() or ""
            except Exception as exc:
                raise DocumentProcessingError(
                    f"Could not extract text from page {index}", retryable=False
                ) from exc
            cleaned = clean_page_text(raw_text)
            if cleaned:
                page_texts[index] = cleaned
            else:
                pages_without_text.append(index)

        if pages_without_text and options.enabled:
            page_texts.update(_ocr_pages(data, pages_without_text, options, ocr_engine))

        pages = [
            ExtractedPage(page_number=page_number, text=page_texts[page_number])
            for page_number in sorted(page_texts)
        ]
        if not pages:
            if pages_without_text and not options.enabled:
                raise DocumentProcessingError(
                    "No selectable text was found in this PDF. Scanned documents require OCR.",
                    retryable=False,
                )
            raise DocumentProcessingError(
                "No usable text could be extracted from this PDF", retryable=False
            )
        return PdfExtraction(pages=pages, page_count=len(reader.pages))
    except DocumentProcessingError:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise DocumentProcessingError("The PDF could not be read", retryable=False) from exc

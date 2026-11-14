from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import anyio
from fastapi import UploadFile

from app.core.exceptions import BadRequestError, PayloadTooLargeError, UnsupportedMediaTypeError

ALLOWED_CONTENT_TYPES = {"application/pdf"}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_READ_CHUNK_SIZE = 1024 * 1024


@dataclass(slots=True)
class ValidatedUpload:
    filename: str
    content_type: str
    temporary_path: Path
    size: int
    checksum: str

    def cleanup(self) -> None:
        self.temporary_path.unlink(missing_ok=True)


def safe_filename(filename: str) -> str:
    base = filename.replace("\\", "/").split("/")[-1]
    base = _CONTROL_CHARACTERS.sub("", base).strip().strip(".")
    if not base:
        raise BadRequestError("The uploaded file must have a valid filename")
    if len(base) > 255:
        stem, extension = os.path.splitext(base)
        base = stem[: 255 - len(extension)] + extension
    return base


def validate_pdf_metadata(filename: str, content_type: str | None) -> tuple[str, str]:
    normalized_filename = safe_filename(filename)
    if not normalized_filename.lower().endswith(".pdf"):
        raise UnsupportedMediaTypeError("Only PDF files with a .pdf extension are accepted")
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedMediaTypeError(
            "Only files with the application/pdf MIME type are accepted"
        )
    return normalized_filename, normalized_type


async def validate_and_spool_upload(file: UploadFile, max_bytes: int) -> ValidatedUpload:
    if not file.filename:
        raise BadRequestError("A file is required")
    filename, content_type = validate_pdf_metadata(file.filename, file.content_type)

    file_descriptor, temporary_name = tempfile.mkstemp(prefix="documind_", suffix=".pdf")
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    header = bytearray()

    try:
        async with await anyio.open_file(temporary_path, "wb") as target:
            while chunk := await file.read(_READ_CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise PayloadTooLargeError(
                        f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB"
                    )
                if len(header) < 1024:
                    header.extend(chunk[: 1024 - len(header)])
                digest.update(chunk)
                await target.write(chunk)

        if size == 0:
            raise BadRequestError("The uploaded PDF is empty")
        if b"%PDF-" not in header:
            raise UnsupportedMediaTypeError("The uploaded file is not a valid PDF document")

        return ValidatedUpload(
            filename=filename,
            content_type=content_type,
            temporary_path=temporary_path,
            size=size,
            checksum=digest.hexdigest(),
        )
    except Exception:
        await asyncio.to_thread(temporary_path.unlink, missing_ok=True)
        raise
    finally:
        await file.close()

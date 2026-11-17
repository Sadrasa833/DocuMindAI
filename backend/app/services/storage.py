from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    pass


def _validate_key(key: str) -> str:
    relative = PurePosixPath(key)
    if relative.is_absolute() or ".." in relative.parts or not key:
        raise StorageError("Invalid object key")
    return relative.as_posix()


class StorageBackend(Protocol):
    async def save_file(self, source: Path, key: str) -> None: ...

    async def read_bytes(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


class LocalStorageBackend:
    """Local filesystem storage with traversal-safe key resolution."""

    def __init__(self, settings: Settings) -> None:
        self.root = settings.storage_directory.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        relative = PurePosixPath(_validate_key(key))
        destination = (self.root / Path(*relative.parts)).resolve()
        if destination != self.root and self.root not in destination.parents:
            raise StorageError("Invalid object key")
        return destination

    async def save_file(self, source: Path, key: str) -> None:
        destination = self._resolve(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            await asyncio.to_thread(shutil.copyfile, source, temporary)
            await asyncio.to_thread(os.replace, temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    async def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError("Stored file was not found") from exc

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(path.unlink, True)

    async def exists(self, key: str) -> bool:
        path = self._resolve(key)
        return await asyncio.to_thread(path.is_file)


def _read_s3_body(body: Any) -> bytes:
    try:
        return body.read()
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()


class S3StorageBackend:
    """S3-compatible object storage (AWS S3, MinIO, and equivalents)."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        if not settings.s3_bucket:
            raise StorageError("S3_BUCKET is required for the S3 storage backend")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise StorageError("The S3 storage backend requires the boto3 package") from exc
            client = boto3.client(
                "s3",
                region_name=settings.s3_region,
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_key,
                use_ssl=settings.s3_use_ssl,
            )
        self.client = client
        self.bucket = settings.s3_bucket
        prefix = settings.s3_prefix.strip("/")
        if ".." in PurePosixPath(prefix).parts:
            raise StorageError("Invalid S3 prefix")
        self.prefix = prefix

    def _key(self, key: str) -> str:
        validated = _validate_key(key)
        return f"{self.prefix}/{validated}" if self.prefix else validated

    async def save_file(self, source: Path, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.upload_file,
                str(source),
                self.bucket,
                self._key(key),
                ExtraArgs={"ContentType": "application/pdf"},
            )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Could not store the uploaded file") from exc

    async def read_bytes(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket,
                Key=self._key(key),
            )
            return await asyncio.to_thread(_read_s3_body, response["Body"])
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Stored file was not found") from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=self.bucket,
                Key=self._key(key),
            )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Could not delete the stored file") from exc

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=self._key(key),
            )
            return True
        except StorageError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise StorageError("Could not check the stored file") from exc


def create_storage_backend(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "s3":
        return S3StorageBackend(settings)
    return LocalStorageBackend(settings)

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    pass


class StorageBackend(Protocol):
    async def save_file(self, source: Path, key: str) -> None: ...

    async def read_bytes(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


class LocalStorageBackend:
    """Local filesystem storage with traversal-safe key resolution.

    The interface intentionally mirrors an object store so an S3 implementation
    can be introduced without changing document or processing services.
    """

    def __init__(self, settings: Settings) -> None:
        self.root = settings.storage_directory.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        relative = PurePosixPath(key)
        if relative.is_absolute() or ".." in relative.parts or not key:
            raise StorageError("Invalid object key")
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

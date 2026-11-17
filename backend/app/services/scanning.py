from __future__ import annotations

import asyncio
import socket
import struct
from pathlib import Path
from typing import Protocol

from app.core.exceptions import MalwareDetectedError, MalwareScannerUnavailableError

_READ_SIZE = 64 * 1024
_MAX_RESPONSE_BYTES = 4096


class MalwareScanner(Protocol):
    async def scan(self, path: Path) -> None: ...


class ClamAVScanner:
    """Scan uploads through ClamAV's INSTREAM protocol."""

    def __init__(self, *, host: str, port: int, timeout_seconds: float) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds

    async def scan(self, path: Path) -> None:
        await asyncio.to_thread(self._scan_sync, path)

    def _scan_sync(self, path: Path) -> None:
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout_seconds
            ) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(b"zINSTREAM\x00")
                with path.open("rb") as source:
                    while chunk := source.read(_READ_SIZE):
                        connection.sendall(struct.pack("!I", len(chunk)) + chunk)
                connection.sendall(struct.pack("!I", 0))
                response = connection.recv(_MAX_RESPONSE_BYTES)
        except OSError as exc:
            raise MalwareScannerUnavailableError(
                "The malware scanner is temporarily unavailable"
            ) from exc

        if b"FOUND" in response:
            raise MalwareDetectedError("The uploaded file failed malware scanning")
        if b"OK" not in response:
            raise MalwareScannerUnavailableError("The malware scanner returned an invalid response")

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from app.core.exceptions import MalwareDetectedError, MalwareScannerUnavailableError
from app.services.scanning import ClamAVScanner


class FakeConnection:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = bytearray()
        self.timeout: float | None = None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, _size: int) -> bytes:
        return self.response


def make_scanner() -> ClamAVScanner:
    return ClamAVScanner(host="clamav", port=3310, timeout_seconds=5)


async def test_clamav_scanner_streams_file_and_accepts_clean_response(
    tmp_path: Path, monkeypatch
) -> None:
    connection = FakeConnection(b"stream: OK\0")

    def connect(_address: tuple[str, int], timeout: float) -> FakeConnection:
        connection.timeout = timeout
        return connection

    monkeypatch.setattr("app.services.scanning.socket.create_connection", connect)
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"PDF content")

    await make_scanner().scan(source)

    assert connection.sent.startswith(b"zINSTREAM\x00")
    assert connection.sent.endswith(struct.pack("!I", 0))
    assert b"PDF content" in connection.sent
    assert connection.timeout == 5


async def test_clamav_scanner_rejects_malware(tmp_path: Path, monkeypatch) -> None:
    connection = FakeConnection(b"stream: Eicar-Test-Signature FOUND\0")
    monkeypatch.setattr(
        "app.services.scanning.socket.create_connection",
        lambda *_args, **_kwargs: connection,
    )
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"malware")

    with pytest.raises(MalwareDetectedError):
        await make_scanner().scan(source)


async def test_clamav_scanner_fails_closed_on_invalid_response(tmp_path: Path, monkeypatch) -> None:
    connection = FakeConnection(b"unexpected")
    monkeypatch.setattr(
        "app.services.scanning.socket.create_connection",
        lambda *_args, **_kwargs: connection,
    )
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"content")

    with pytest.raises(MalwareScannerUnavailableError):
        await make_scanner().scan(source)

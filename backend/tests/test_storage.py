from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.services.storage import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageError,
    create_storage_backend,
)
from tests.conftest import settings


class FakeS3Error(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self) -> bytes:
        return self._stream.read()


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_file(
        self,
        source: str,
        bucket: str,
        key: str,
        ExtraArgs: dict[str, str] | None = None,
    ) -> None:
        assert bucket == "documents"
        assert ExtraArgs == {"ContentType": "application/pdf"}
        self.objects[key] = Path(source).read_bytes()

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeBody]:
        assert Bucket == "documents"
        try:
            return {"Body": FakeBody(self.objects[Key])}
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc

    def head_object(self, *, Bucket: str, Key: str) -> None:
        assert Bucket == "documents"
        if Key not in self.objects:
            raise FakeS3Error("404")

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        assert Bucket == "documents"
        self.objects.pop(Key, None)


def s3_settings() -> Settings:
    return settings.model_copy(
        update={
            "storage_backend": "s3",
            "s3_bucket": "documents",
            "s3_prefix": "tenant-a",
        }
    )


async def test_local_storage_round_trip_and_traversal_rejection(tmp_path: Path) -> None:
    storage = LocalStorageBackend(settings)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nlocal content")

    await storage.save_file(source, "user/document.pdf")
    assert await storage.exists("user/document.pdf") is True
    assert await storage.read_bytes("user/document.pdf") == b"%PDF-1.7\nlocal content"

    await storage.delete("user/document.pdf")
    assert await storage.exists("user/document.pdf") is False

    for key in ("../escape.pdf", "/absolute.pdf", ""):
        with pytest.raises(StorageError, match="Invalid object key"):
            await storage.read_bytes(key)


async def test_s3_storage_round_trip_uses_prefix(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3StorageBackend(s3_settings(), client=client)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\nremote content")

    await storage.save_file(source, "user/document.pdf")
    assert client.objects == {"tenant-a/user/document.pdf": b"%PDF-1.7\nremote content"}

    assert await storage.exists("user/document.pdf") is True
    assert await storage.read_bytes("user/document.pdf") == b"%PDF-1.7\nremote content"

    await storage.delete("user/document.pdf")
    assert await storage.exists("user/document.pdf") is False
    with pytest.raises(StorageError, match="not found"):
        await storage.read_bytes("user/document.pdf")


async def test_s3_storage_rejects_traversal_and_missing_bucket(tmp_path: Path) -> None:
    storage = S3StorageBackend(s3_settings(), client=FakeS3Client())
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\ncontent")

    with pytest.raises(StorageError, match="Invalid object key"):
        await storage.save_file(source, "../escape.pdf")

    with pytest.raises(StorageError, match="S3_BUCKET is required"):
        S3StorageBackend(settings.model_copy(update={"s3_bucket": None}), client=FakeS3Client())


def test_storage_factory_defaults_to_local() -> None:
    assert isinstance(create_storage_backend(settings), LocalStorageBackend)


def test_storage_factory_builds_s3_client_from_settings(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def client(service: str, **kwargs: object) -> FakeS3Client:
        captured["service"] = service
        captured.update(kwargs)
        return FakeS3Client()

    fake_boto3 = SimpleNamespace(client=client)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    configured = s3_settings().model_copy(
        update={
            "s3_endpoint_url": "http://minio:9000",
            "s3_access_key_id": "access",
            "s3_secret_access_key": SecretStr("secret"),
            "s3_use_ssl": False,
        }
    )

    storage = create_storage_backend(configured)

    assert isinstance(storage, S3StorageBackend)
    assert captured == {
        "service": "s3",
        "region_name": "us-east-1",
        "endpoint_url": "http://minio:9000",
        "aws_access_key_id": "access",
        "aws_secret_access_key": "secret",
        "use_ssl": False,
    }

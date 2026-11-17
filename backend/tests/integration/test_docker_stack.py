from __future__ import annotations

import io
import os
import time
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from PIL import Image
from pypdf import PdfReader

from tests.fakes import make_pdf, make_scanned_pdf

DOCKER_E2E_BASE_URL = os.environ.get("DOCKER_E2E_BASE_URL")
DOCKER_E2E_DATABASE_URL = os.environ.get("DOCKER_E2E_DATABASE_URL")
_PERSIAN_OCR_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "persian-scanned.png"


def _make_persian_scanned_pdf() -> bytes:
    buffer = io.BytesIO()
    with Image.open(_PERSIAN_OCR_IMAGE) as image:
        image.convert("RGB").save(buffer, "PDF", resolution=200.0)
    return buffer.getvalue()


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not DOCKER_E2E_BASE_URL,
        reason="Set DOCKER_E2E_BASE_URL to run Docker/Celery integration tests",
    ),
]

_DOCUMENT_TEXT = (
    "Acme Services Agreement. Payment terms are net thirty days from the invoice date. "
    "Late payments accrue interest at one percent per month."
)
_PROCESSING_TIMEOUT_SECONDS = 120


def _wait_for_status(client: httpx.Client, document_id: str, expected: str) -> dict:
    deadline = time.monotonic() + _PROCESSING_TIMEOUT_SECONDS
    last_payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/documents/{document_id}/status")
        assert response.status_code == 200, response.text
        last_payload = response.json()
        if last_payload["status"] == expected:
            return last_payload
        if last_payload["status"] == "failed":
            pytest.fail(f"Document processing failed while waiting for {expected}: {last_payload}")
        time.sleep(1)
    pytest.fail(f"Document did not reach {expected} in time: {last_payload}")


def test_docker_celery_upload_search_and_chat_round_trip() -> None:
    assert DOCKER_E2E_BASE_URL is not None
    email = f"docker-e2e-{uuid.uuid4().hex[:12]}@example.com"

    with httpx.Client(base_url=DOCKER_E2E_BASE_URL, timeout=30.0, follow_redirects=True) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123",
                "full_name": "Docker E2E",
            },
        )
        assert registered.status_code == 201, registered.text

        upload = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "docker-e2e.pdf",
                    make_pdf([_DOCUMENT_TEXT]),
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()

        processed = _wait_for_status(client, document["id"], "completed")
        assert processed["page_count"] == 1
        assert processed["chunk_count"] >= 1

        search = client.post(
            "/api/v1/search",
            json={
                "query": "payment terms net thirty days invoice date",
                "document_ids": [document["id"]],
                "top_k": 5,
            },
        )
        assert search.status_code == 200, search.text
        results = search.json()["results"]
        assert results
        assert results[0]["document_id"] == document["id"]
        assert results[0]["page_number"] == 1

        chat = client.post(
            "/api/v1/chat",
            json={
                "question": "What are the payment terms and when is payment due?",
                "document_ids": [document["id"]],
            },
        )
        assert chat.status_code == 200, chat.text
        payload = chat.json()
        assert "[S1]" in payload["answer"]
        assert payload["citations"]
        citation = payload["citations"][0]
        assert citation["document_id"] == document["id"]
        assert citation["page_number"] == 1
        assert citation["document_name"] == "docker-e2e.pdf"

        conversation = client.get(f"/api/v1/conversations/{payload['conversation_id']}")
        assert conversation.status_code == 200, conversation.text
        messages = conversation.json()["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert "[S1]" in messages[-1]["content"]


def test_docker_celery_ocrs_an_image_only_pdf() -> None:
    assert DOCKER_E2E_BASE_URL is not None
    scanned = make_scanned_pdf([_DOCUMENT_TEXT])
    assert not "".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(scanned)).pages
    ).strip()

    email = f"docker-e2e-ocr-{uuid.uuid4().hex[:12]}@example.com"

    with httpx.Client(base_url=DOCKER_E2E_BASE_URL, timeout=30.0, follow_redirects=True) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123",
                "full_name": "Docker E2E OCR",
            },
        )
        assert registered.status_code == 201, registered.text

        upload = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "docker-scanned.pdf",
                    scanned,
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()

        processed = _wait_for_status(client, document["id"], "completed")
        assert processed["page_count"] == 1
        assert processed["chunk_count"] >= 1

        search = client.post(
            "/api/v1/search",
            json={
                "query": "payment terms invoice date",
                "document_ids": [document["id"]],
                "top_k": 5,
            },
        )
        assert search.status_code == 200, search.text
        results = search.json()["results"]
        assert results
        content = " ".join(result["content"] for result in results).lower()
        assert any(word in content for word in ("payment", "invoice", "agreement"))


@pytest.mark.skipif(
    not DOCKER_E2E_DATABASE_URL,
    reason="Set DOCKER_E2E_DATABASE_URL to verify Persian OCR output",
)
def test_docker_celery_ocrs_a_persian_scanned_pdf() -> None:
    assert DOCKER_E2E_BASE_URL is not None
    assert DOCKER_E2E_DATABASE_URL is not None
    scanned = _make_persian_scanned_pdf()
    assert not "".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(scanned)).pages
    ).strip()

    email = f"docker-e2e-persian-{uuid.uuid4().hex[:12]}@example.com"

    with httpx.Client(base_url=DOCKER_E2E_BASE_URL, timeout=30.0, follow_redirects=True) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123",
                "full_name": "Docker E2E Persian OCR",
            },
        )
        assert registered.status_code == 201, registered.text

        upload = client.post(
            "/api/v1/documents/upload",
            files={"file": ("persian-scanned.pdf", scanned, "application/pdf")},
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()

        processed = _wait_for_status(client, document["id"], "completed")
        assert processed["page_count"] == 1
        assert processed["chunk_count"] >= 1

    with psycopg.connect(DOCKER_E2E_DATABASE_URL) as connection:
        rows = connection.execute(
            "SELECT content FROM document_chunks WHERE document_id = %s",
            (uuid.UUID(document["id"]),),
        ).fetchall()
    content = "\n".join(row[0] for row in rows)
    assert "قرارداد" in content
    assert "پرداخت" in content
    assert "سی روز" in content


def test_docker_celery_marks_unprocessable_document_failed() -> None:
    assert DOCKER_E2E_BASE_URL is not None
    email = f"docker-e2e-empty-{uuid.uuid4().hex[:12]}@example.com"

    with httpx.Client(base_url=DOCKER_E2E_BASE_URL, timeout=30.0, follow_redirects=True) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123",
                "full_name": "Docker E2E Empty",
            },
        )
        assert registered.status_code == 201, registered.text

        upload = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "docker-empty.pdf",
                    make_pdf([""]),
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()

        failed = _wait_for_status(client, document["id"], "failed")
        assert "text" in (failed["error_message"] or "").lower()
        assert failed["chunk_count"] == 0


@pytest.mark.skipif(
    not DOCKER_E2E_DATABASE_URL,
    reason="Set DOCKER_E2E_DATABASE_URL to verify celery-beat recovery",
)
def test_docker_beat_recovers_a_stuck_processing_document() -> None:
    assert DOCKER_E2E_BASE_URL is not None
    assert DOCKER_E2E_DATABASE_URL is not None
    email = f"docker-e2e-stuck-{uuid.uuid4().hex[:12]}@example.com"

    with httpx.Client(base_url=DOCKER_E2E_BASE_URL, timeout=30.0, follow_redirects=True) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123",
                "full_name": "Docker E2E Stuck",
            },
        )
        assert registered.status_code == 201, registered.text

        upload = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "docker-stuck.pdf",
                    make_pdf([_DOCUMENT_TEXT]),
                    "application/pdf",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()
        _wait_for_status(client, document["id"], "completed")

        with psycopg.connect(DOCKER_E2E_DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                "UPDATE documents SET status = 'processing', "
                "processing_started_at = now() - interval '2 hours', "
                "processing_attempt_id = %s, chunk_count = 0, page_count = NULL "
                "WHERE id = %s",
                (uuid.uuid4(), uuid.UUID(document["id"])),
            )

        recovered = _wait_for_status(client, document["id"], "completed")
        assert recovered["page_count"] == 1
        assert recovered["chunk_count"] >= 1

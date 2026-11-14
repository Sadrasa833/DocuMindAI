from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.conftest import TEST_UPLOAD_PATH, fake_dispatcher, settings
from tests.fakes import DEFAULT_PAGES, make_pdf, register_user, upload_pdf


async def test_upload_document_queues_processing(client: AsyncClient) -> None:
    await register_user(client)
    response = await upload_pdf(client, filename="../../Quarterly Report.pdf")

    assert response.status_code == 201, response.text
    document = response.json()
    assert document["original_filename"] == "Quarterly Report.pdf"
    assert document["mime_type"] == "application/pdf"
    assert document["status"] == "pending"
    assert document["file_size"] > 0
    assert document["chunk_count"] == 0
    assert "storage_key" not in document
    assert fake_dispatcher.enqueued == [uuid.UUID(document["id"])]
    assert list(TEST_UPLOAD_PATH.rglob("*.pdf"))

    status = await client.get(f"/api/v1/documents/{document['id']}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "pending"

    detail = await client.get(f"/api/v1/documents/{document['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == document["id"]


async def test_upload_rejects_invalid_files(client: AsyncClient) -> None:
    await register_user(client)

    text_extension = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.txt", b"hello", "application/pdf")},
    )
    assert text_extension.status_code == 415

    wrong_mime = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("agreement.pdf", make_pdf(["hello"]), "text/plain")},
    )
    assert wrong_mime.status_code == 415

    fake_pdf = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("agreement.pdf", b"not really a pdf", "application/pdf")},
    )
    assert fake_pdf.status_code == 415

    empty = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty.status_code == 400

    assert fake_dispatcher.enqueued == []


async def test_upload_enforces_size_limit(client: AsyncClient, monkeypatch) -> None:
    await register_user(client)
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    oversized = b"%PDF-1.7\n" + b"x" * (1024 * 1024)
    response = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("large.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_list_filter_sort_and_stats(client: AsyncClient, process_document) -> None:
    await register_user(client)
    first = (await upload_pdf(client, filename="alpha.pdf")).json()
    second = (await upload_pdf(client, filename="beta.pdf")).json()
    third = (await upload_pdf(client, filename="gamma.pdf")).json()
    await process_document(uuid.UUID(second["id"]))

    all_docs = await client.get("/api/v1/documents")
    assert all_docs.status_code == 200
    payload = all_docs.json()
    assert payload["total"] == 3
    assert payload["page"] == 1
    assert payload["pages"] == 1
    assert len(payload["items"]) == 3

    completed = await client.get("/api/v1/documents", params={"status": "completed"})
    assert [item["id"] for item in completed.json()["items"]] == [second["id"]]

    pending = await client.get("/api/v1/documents", params={"status": "pending"})
    assert {item["id"] for item in pending.json()["items"]} == {
        first["id"],
        third["id"],
    }

    searched = await client.get("/api/v1/documents", params={"search": "alp"})
    assert [item["id"] for item in searched.json()["items"]] == [first["id"]]

    sorted_docs = await client.get(
        "/api/v1/documents",
        params={"sort_by": "original_filename", "sort_order": "asc"},
    )
    assert [item["original_filename"] for item in sorted_docs.json()["items"]] == [
        "alpha.pdf",
        "beta.pdf",
        "gamma.pdf",
    ]

    paginated = await client.get("/api/v1/documents", params={"page": 2, "page_size": 2})
    assert paginated.json()["total"] == 3
    assert paginated.json()["pages"] == 2
    assert len(paginated.json()["items"]) == 1

    stats = await client.get("/api/v1/documents/stats")
    assert stats.status_code == 200
    assert stats.json()["total_documents"] == 3
    assert stats.json()["completed_documents"] == 1
    assert stats.json()["processing_documents"] == 2
    assert stats.json()["failed_documents"] == 0
    assert stats.json()["total_chunks"] > 0
    assert stats.json()["total_pages"] == 2


async def test_document_ownership_is_enforced(client_factory) -> None:
    alice = await client_factory()
    bob = await client_factory()
    await register_user(alice, email="alice@example.com")
    await register_user(bob, email="bob@example.com")
    document = (await upload_pdf(alice)).json()
    document_id = document["id"]

    for path in (
        f"/api/v1/documents/{document_id}",
        f"/api/v1/documents/{document_id}/status",
    ):
        response = await bob.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    missing_delete = await bob.delete(f"/api/v1/documents/{document_id}")
    assert missing_delete.status_code == 404
    assert (await alice.get(f"/api/v1/documents/{document_id}")).status_code == 200

    missing_id = uuid.uuid4()
    assert (await alice.get(f"/api/v1/documents/{missing_id}")).status_code == 404


async def test_delete_document_removes_file_and_chunks(client_factory, process_document) -> None:
    alice = await client_factory()
    await register_user(alice)
    document = (await upload_pdf(alice)).json()
    await process_document(uuid.UUID(document["id"]))

    assert list(TEST_UPLOAD_PATH.rglob("*.pdf"))
    deleted = await alice.delete(f"/api/v1/documents/{document['id']}")
    assert deleted.status_code == 204
    assert (await alice.get(f"/api/v1/documents/{document['id']}")).status_code == 404
    assert not list(TEST_UPLOAD_PATH.rglob("*.pdf"))

    search = await alice.post("/api/v1/search", json={"query": "payment terms", "top_k": 5})
    assert search.status_code == 200
    assert search.json()["results"] == []


async def test_unauthenticated_document_access_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/documents")
    assert response.status_code == 401
    upload = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("agreement.pdf", make_pdf(DEFAULT_PAGES), "application/pdf")},
    )
    assert upload.status_code == 401

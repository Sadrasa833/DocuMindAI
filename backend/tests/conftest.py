from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
TEST_DB_PATH = BACKEND_DIR / "documind_test.db"
TEST_UPLOAD_PATH = BACKEND_DIR / ".test_uploads"

TEST_DB_PATH.unlink(missing_ok=True)
shutil.rmtree(TEST_UPLOAD_PATH, ignore_errors=True)

os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz"
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["STORAGE_PATH"] = str(TEST_UPLOAD_PATH)
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["CORS_ORIGINS"] = "http://testserver"
os.environ["OPENAI_CHAT_MODEL"] = "test-chat-model"
os.environ["OPENAI_EMBEDDING_MODEL"] = "test-embedding-model"

from app.api.deps import (  # noqa: E402
    get_chat_provider,
    get_embedding_provider,
    get_storage_backend,
    get_task_dispatcher,
)
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.embeddings import EmbeddingService  # noqa: E402
from app.services.processing import ProcessingService  # noqa: E402
from app.services.storage import LocalStorageBackend  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeChatProvider,
    FakeEmbeddingProvider,
    FakeTaskDispatcher,
    make_embedding_service,
)

settings = get_settings()
fake_dispatcher = FakeTaskDispatcher()
fake_embedding_provider = FakeEmbeddingProvider(settings.embedding_dimensions)
fake_chat_provider = FakeChatProvider()


def _embedding_provider_override() -> FakeEmbeddingProvider:
    return fake_embedding_provider


def _chat_provider_override() -> FakeChatProvider:
    return fake_chat_provider


def _dispatcher_override() -> FakeTaskDispatcher:
    return fake_dispatcher


def _storage_override() -> LocalStorageBackend:
    return LocalStorageBackend(settings)


app.dependency_overrides[get_embedding_provider] = _embedding_provider_override
app.dependency_overrides[get_chat_provider] = _chat_provider_override
app.dependency_overrides[get_task_dispatcher] = _dispatcher_override
app.dependency_overrides[get_storage_backend] = _storage_override


@pytest_asyncio.fixture(autouse=True)
async def database_schema() -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
def reset_fakes() -> AsyncIterator[None]:
    fake_dispatcher.enqueued.clear()
    fake_embedding_provider.calls.clear()
    fake_chat_provider.calls.clear()
    fake_chat_provider.response = "The documents state that payment is due within 30 days [S1]."
    shutil.rmtree(TEST_UPLOAD_PATH, ignore_errors=True)
    TEST_UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    yield


@pytest_asyncio.fixture
async def client_factory() -> AsyncIterator[Callable[[], AsyncClient]]:
    clients: list[AsyncClient] = []

    async def factory() -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"User-Agent": "DocuMind-Test-Client/1.0"},
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


@pytest_asyncio.fixture
async def client(client_factory: Callable[[], AsyncClient]) -> AsyncClient:
    return await client_factory()


@pytest_asyncio.fixture
async def process_document() -> Callable[[uuid.UUID], object]:
    async def process(document_id: uuid.UUID) -> bool:
        async with AsyncSessionLocal() as session:
            service = ProcessingService(
                session,
                settings=settings,
                storage=LocalStorageBackend(settings),
                embedding_service=make_embedding_service(fake_embedding_provider),
            )
            return await service.process(document_id)

    return process


@pytest_asyncio.fixture
async def processed_document(
    process_document: Callable[[uuid.UUID], object],
) -> Callable[..., object]:
    from tests.fakes import upload_pdf

    async def create(
        client: AsyncClient,
        pages: list[str] | None = None,
        filename: str = "agreement.pdf",
    ) -> dict[str, object]:
        response = await upload_pdf(client, pages, filename=filename)
        assert response.status_code == 201, response.text
        document_id = response.json()["id"]
        await process_document(uuid.UUID(document_id))
        detail = await client.get(f"/api/v1/documents/{document_id}")
        assert detail.status_code == 200, detail.text
        return detail.json()

    return create


@pytest.fixture
def chat_provider() -> FakeChatProvider:
    return fake_chat_provider


@pytest.fixture
def embedding_provider() -> FakeEmbeddingProvider:
    return fake_embedding_provider


@pytest.fixture
def test_settings() -> object:
    return settings


def embedding_service(provider: FakeEmbeddingProvider | None = None) -> EmbeddingService:
    return make_embedding_service(provider or fake_embedding_provider)

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.models.user import User
from app.repositories.chunks import ChunkRepository

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set TEST_DATABASE_URL to run PostgreSQL/pgvector integration tests",
    ),
]
DIMENSIONS = 1536


def _sync_url() -> str:
    assert TEST_DATABASE_URL is not None
    return TEST_DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> str:
    assert TEST_DATABASE_URL is not None
    with psycopg.connect(_sync_url(), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")

    environment = os.environ.copy()
    environment["DATABASE_URL"] = TEST_DATABASE_URL
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _vector(first: float = 1.0, second: float = 0.0) -> list[float]:
    values = [0.0] * DIMENSIONS
    values[0] = first
    values[1] = second
    return values


async def _create_document_with_chunks(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    filename: str,
    embeddings: list[list[float]],
    status: DocumentStatus = DocumentStatus.COMPLETED,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    async with factory() as session:
        user = User(
            email=email,
            full_name=email.split("@")[0].title(),
            password_hash="not-used-in-integration-tests",
        )
        session.add(user)
        await session.flush()
        document = Document(
            user_id=user.id,
            original_filename=filename,
            storage_key=f"{user.id}/{uuid.uuid4()}.pdf",
            mime_type="application/pdf",
            file_size=128,
            file_hash=uuid.uuid4().hex,
            status=status,
            chunk_count=len(embeddings),
        )
        session.add(document)
        await session.flush()
        chunks = [
            DocumentChunk(
                document_id=document.id,
                user_id=user.id,
                content=f"chunk {index} for {filename}",
                page_number=index + 1,
                chunk_index=index,
                embedding=embedding,
            )
            for index, embedding in enumerate(embeddings)
        ]
        session.add_all(chunks)
        await session.commit()
        return user.id, document.id, [chunk.id for chunk in chunks]


@pytest.mark.asyncio
async def test_migrations_install_pgvector_and_hnsw_index(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        extension = (
            await session.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar_one()
        version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        index = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'document_chunks' "
                    "AND indexname = 'ix_document_chunks_embedding_hnsw'"
                )
            )
        ).scalar_one()
        columns = (
            await session.execute(
                text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
                )
            )
        ).scalar_one()

    assert extension.startswith("0.")
    assert version == "0005_async_summaries"
    assert "USING hnsw" in index
    assert "vector_cosine_ops" in index
    assert columns == f"vector({DIMENSIONS})"


@pytest.mark.asyncio
async def test_vector_storage_dimensions_and_cosine_search(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, _, chunk_ids = await _create_document_with_chunks(
        session_factory,
        email="vector@example.com",
        filename="vectors.pdf",
        embeddings=[_vector(1.0, 0.0), _vector(0.0, 1.0)],
    )

    async with session_factory() as session:
        repository = ChunkRepository(session)
        hits = await repository.semantic_search(
            query_embedding=_vector(1.0, 0.0),
            user_id=user_id,
            document_ids=None,
            top_k=2,
        )

    assert [hit.chunk.id for hit in hits] == chunk_ids
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-6)
    assert hits[1].similarity == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio
async def test_embedding_dimension_mismatch_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, document_id, _ = await _create_document_with_chunks(
        session_factory,
        email="dimensions@example.com",
        filename="dimensions.pdf",
        embeddings=[_vector()],
    )

    async with session_factory() as session:
        session.add(
            DocumentChunk(
                document_id=document_id,
                user_id=user_id,
                content="wrong dimensions",
                page_number=2,
                chunk_index=1,
                embedding=[0.1] * (DIMENSIONS - 1),
            )
        )
        with pytest.raises(DBAPIError) as error:
            await session.commit()

    assert "dimension" in str(error.value).lower()


@pytest.mark.asyncio
async def test_user_and_document_isolation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alice_id, alice_doc, alice_chunks = await _create_document_with_chunks(
        session_factory,
        email="alice@example.com",
        filename="alice.pdf",
        embeddings=[_vector(1.0, 0.0)],
    )
    _, bob_doc, _ = await _create_document_with_chunks(
        session_factory,
        email="bob@example.com",
        filename="bob.pdf",
        embeddings=[_vector(1.0, 0.0)],
    )

    async with session_factory() as session:
        repository = ChunkRepository(session)
        alice_hits = await repository.semantic_search(
            query_embedding=_vector(1.0, 0.0),
            user_id=alice_id,
            document_ids=None,
            top_k=10,
        )
        filtered_hits = await repository.semantic_search(
            query_embedding=_vector(1.0, 0.0),
            user_id=alice_id,
            document_ids=[bob_doc],
            top_k=10,
        )

    assert [hit.chunk.id for hit in alice_hits] == alice_chunks
    assert all(hit.chunk.document_id == alice_doc for hit in alice_hits)
    assert filtered_hits == []


@pytest.mark.asyncio
async def test_hnsw_index_can_serve_vector_ordering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _create_document_with_chunks(
        session_factory,
        email="hnsw@example.com",
        filename="hnsw.pdf",
        embeddings=[_vector(1.0, 0.0), _vector(0.0, 1.0)],
    )

    async with session_factory() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        result = await session.execute(
            text(
                "EXPLAIN SELECT id FROM document_chunks "
                "ORDER BY embedding <=> CAST(:query AS vector) LIMIT 5"
            ),
            {"query": "[" + ",".join("0" for _ in range(DIMENSIONS)) + "]"},
        )
        plan = "\n".join(row[0] for row in result.all())

    assert "ix_document_chunks_embedding_hnsw" in plan

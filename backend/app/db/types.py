from __future__ import annotations

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class EmbeddingVector(TypeDecorator[list[float]]):
    """Portable vector column: pgvector in PostgreSQL, JSON in SQLite tests.

    The application deliberately uses its own type decorator so the rest of the
    codebase stays portable while production uses a native pgvector column and
    an HNSW cosine index.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dimensions))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return list(value)

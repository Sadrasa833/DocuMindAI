"""Add processing attempt ids for safe worker recovery.

Revision ID: 0003_processing_attempts
Revises: 0002_refresh_token_families
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_processing_attempts"
down_revision: str | None = "0002_refresh_token_families"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("processing_attempt_id", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "processing_attempt_id")

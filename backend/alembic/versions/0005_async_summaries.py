"""Add background summary job state.

Revision ID: 0005_async_summaries
Revises: 0004_account_management
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_async_summaries"
down_revision: str | None = "0004_account_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

summary_status = sa.Enum(
    "none",
    "pending",
    "processing",
    "completed",
    "failed",
    name="summary_status",
    native_enum=False,
    length=20,
)


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "summary_status",
            summary_status,
            server_default=sa.text("'none'"),
            nullable=False,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("summary_error", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("summary_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("summary_attempt_id", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "summary_attempt_id")
    op.drop_column("documents", "summary_started_at")
    op.drop_column("documents", "summary_error")
    op.drop_column("documents", "summary_status")

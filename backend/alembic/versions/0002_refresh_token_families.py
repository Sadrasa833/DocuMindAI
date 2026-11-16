"""Add refresh-token families and revocation reasons.

Revision ID: 0002_refresh_token_families
Revises: 0001_initial
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_refresh_token_families"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column("family_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
    )
    # Existing tokens predate rotation families. Giving each one its own family
    # preserves their current behavior without linking unrelated sessions.
    op.execute("UPDATE refresh_tokens SET family_id = id WHERE family_id IS NULL")
    op.alter_column("refresh_tokens", "family_id", nullable=False)
    op.create_index(
        "ix_refresh_tokens_family_active",
        "refresh_tokens",
        ["family_id", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_active", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "revoked_reason")
    op.drop_column("refresh_tokens", "family_id")

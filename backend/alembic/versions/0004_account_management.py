"""Add email verification tokens and verified user state.

Revision ID: 0004_account_management
Revises: 0003_processing_attempts
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_account_management"
down_revision: str | None = "0003_processing_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

account_token_purpose = sa.Enum(
    "email_verification",
    "password_reset",
    name="account_token_purpose",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "account_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", account_token_purpose, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_tokens"),
    )
    op.create_index("ix_account_tokens_user_id", "account_tokens", ["user_id"], unique=False)
    op.create_index(
        "ix_account_tokens_token_hash",
        "account_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_account_tokens_user_purpose",
        "account_tokens",
        ["user_id", "purpose", "used_at"],
        unique=False,
    )
    op.create_index(
        "ix_account_tokens_expires",
        "account_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("account_tokens")
    op.drop_column("users", "email_verified_at")

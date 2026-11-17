from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import UUIDPrimaryKeyMixin, utc_now
from app.models.enums import AccountTokenPurpose

if TYPE_CHECKING:
    from app.models.user import User


class AccountToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "account_tokens"
    __table_args__ = (
        Index("ix_account_tokens_user_purpose", "user_id", "purpose", "used_at"),
        Index("ix_account_tokens_expires", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[AccountTokenPurpose] = mapped_column(
        Enum(
            AccountTokenPurpose,
            name="account_token_purpose",
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="account_tokens")

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_token import AccountToken
from app.models.enums import AccountTokenPurpose


class AccountTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, token: AccountToken) -> AccountToken:
        self.session.add(token)
        return token

    async def get_by_hash_for_update(
        self,
        token_hash: str,
        *,
        purpose: AccountTokenPurpose,
    ) -> AccountToken | None:
        result = await self.session.execute(
            select(AccountToken)
            .where(
                AccountToken.token_hash == token_hash,
                AccountToken.purpose == purpose,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def consume(self, token_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(AccountToken)
            .where(
                AccountToken.id == token_id,
                AccountToken.used_at.is_(None),
                AccountToken.expires_at > datetime.now(UTC),
            )
            .values(used_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    async def invalidate_unused(
        self,
        user_id: uuid.UUID,
        *,
        purpose: AccountTokenPurpose,
    ) -> None:
        await self.session.execute(
            update(AccountToken)
            .where(
                AccountToken.user_id == user_id,
                AccountToken.purpose == purpose,
                AccountToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )

    async def delete_stale(self, *, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        result = await self.session.execute(
            delete(AccountToken)
            .where((AccountToken.expires_at < cutoff) | (AccountToken.used_at < cutoff))
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

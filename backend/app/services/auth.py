from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository

logger = logging.getLogger(__name__)

_REVOKED_FOR_ROTATION = "rotated"
_REVOKED_FOR_LOGOUT = "logout"
_REVOKED_FOR_REUSE = "reuse"
_REVOKED_FOR_PASSWORD_CHANGE = "password_change"


def _is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _within_reuse_grace(revoked_at: datetime, grace_seconds: int) -> bool:
    if revoked_at.tzinfo is None:
        revoked_at = revoked_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - revoked_at <= timedelta(seconds=grace_seconds)


@dataclass(slots=True)
class AuthTokens:
    user: User
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def _issue_tokens(
        self,
        user: User,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
        family_id: uuid.UUID | None = None,
    ) -> AuthTokens:
        access_token, _ = create_access_token(user.id, self.settings)
        refresh_token, refresh_expires_at = create_refresh_token(user.id, self.settings)
        self.session.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id or uuid.uuid4(),
                token_hash=hash_token(refresh_token),
                expires_at=refresh_expires_at,
                user_agent=(user_agent or "")[:300] or None,
                ip_address=(ip_address or "")[:64] or None,
            )
        )
        return AuthTokens(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires_at,
        )

    async def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthTokens:
        normalized_email = email.strip().lower()
        if await self.users.get_by_email(normalized_email):
            raise ConflictError("An account with this email already exists")
        user = User(
            email=normalized_email,
            full_name=full_name.strip(),
            password_hash=hash_password(password),
        )
        self.users.session.add(user)
        try:
            await self.session.flush()
            tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
            await self.session.commit()
            await self.session.refresh(user)
            return tokens
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("An account with this email already exists") from exc

    async def login(
        self,
        *,
        email: str,
        password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthTokens:
        user = await self.users.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid email or password")
        if not user.is_active:
            raise UnauthorizedError("Invalid email or password")
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        await self.session.commit()
        return tokens

    async def refresh(
        self,
        refresh_token: str | None,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthTokens:
        if not refresh_token:
            raise UnauthorizedError("Refresh token is missing")
        try:
            payload = decode_token(refresh_token, "refresh", self.settings)
            user_id = uuid.UUID(str(payload["sub"]))
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Your session has expired. Please sign in again.") from exc
        except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
            raise UnauthorizedError("Session is invalid. Please sign in again.") from exc

        stored = await self.refresh_tokens.get_by_hash(hash_token(refresh_token), for_update=True)
        if stored is None:
            logger.warning(
                "Rejected unknown refresh token",
                extra={"user_id": str(user_id), "reason": "unknown"},
            )
            raise UnauthorizedError("Session is invalid. Please sign in again.")
        if stored.user_id != user_id:
            logger.warning(
                "Rejected refresh token with mismatched subject",
                extra={"user_id": str(user_id), "reason": "subject_mismatch"},
            )
            raise UnauthorizedError("Session is invalid. Please sign in again.")

        if stored.revoked_at is not None:
            if stored.revoked_reason == _REVOKED_FOR_ROTATION:
                if _within_reuse_grace(
                    stored.revoked_at, self.settings.refresh_reuse_grace_seconds
                ):
                    logger.warning(
                        "Refresh token was rotated concurrently; rejected the racing request",
                        extra={"user_id": str(user_id), "reason": "concurrent"},
                    )
                    raise UnauthorizedError("This session is being refreshed. Please retry.")
                await self.refresh_tokens.revoke_family(stored.family_id, reason=_REVOKED_FOR_REUSE)
                await self.session.commit()
                logger.warning(
                    "Refresh token reuse detected; revoked the session family",
                    extra={"user_id": str(user_id), "reason": "reuse"},
                )
                raise UnauthorizedError(
                    "This session was ended because its refresh token was reused. "
                    "Please sign in again."
                )
            logger.warning(
                "Rejected revoked refresh token",
                extra={"user_id": str(user_id), "reason": stored.revoked_reason or "revoked"},
            )
            raise UnauthorizedError("This session has been revoked. Please sign in again.")

        if _is_expired(stored.expires_at):
            logger.info(
                "Rejected expired refresh token",
                extra={"user_id": str(user_id), "reason": "expired"},
            )
            raise UnauthorizedError("Your session has expired. Please sign in again.")

        user = await self.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Session is invalid. Please sign in again.")

        if not await self.refresh_tokens.revoke_active(stored.id, reason=_REVOKED_FOR_ROTATION):
            logger.warning(
                "Refresh token was rotated concurrently; rejected the racing request",
                extra={"user_id": str(user_id), "reason": "concurrent"},
            )
            raise UnauthorizedError("This session is being refreshed. Please retry.")

        tokens = await self._issue_tokens(
            user,
            user_agent=user_agent,
            ip_address=ip_address,
            family_id=stored.family_id,
        )
        await self.session.commit()
        return tokens

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            await self.refresh_tokens.revoke_by_hash(
                hash_token(refresh_token), reason=_REVOKED_FOR_LOGOUT
            )
            await self.session.commit()

    async def update_profile(self, user: User, full_name: str) -> User:
        user.full_name = full_name.strip()
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def change_password(
        self,
        user: User,
        *,
        current_password: str,
        new_password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> AuthTokens:
        if not verify_password(current_password, user.password_hash):
            raise UnauthorizedError("Current password is incorrect")
        if current_password == new_password:
            raise ConflictError("New password must differ from the current password")
        user.password_hash = hash_password(new_password)
        await self.refresh_tokens.revoke_all_for_user(user.id, reason=_REVOKED_FOR_PASSWORD_CHANGE)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        await self.session.commit()
        return tokens

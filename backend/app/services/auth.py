from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

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
    ) -> AuthTokens:
        access_token, _ = create_access_token(user.id, self.settings)
        refresh_token, refresh_expires_at = create_refresh_token(user.id, self.settings)
        self.session.add(
            RefreshToken(
                user_id=user.id,
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
        except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
            raise UnauthorizedError("Session is invalid or has expired") from exc

        stored = await self.refresh_tokens.get_active_by_hash(
            hash_token(refresh_token), for_update=True
        )
        if stored is None:
            await self.refresh_tokens.revoke_all_for_user(user_id)
            await self.session.commit()
            raise UnauthorizedError("Session is invalid or has expired")
        user = await self.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Session is invalid or has expired")

        await self.refresh_tokens.revoke(stored)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        await self.session.commit()
        return tokens

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            await self.refresh_tokens.revoke_by_hash(hash_token(refresh_token))
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
        await self.refresh_tokens.revoke_all_for_user(user.id)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        await self.session.commit()
        return tokens

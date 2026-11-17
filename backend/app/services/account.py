from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError, UnauthorizedError
from app.core.security import hash_password, hash_token, verify_password
from app.models.account_token import AccountToken
from app.models.enums import AccountTokenPurpose
from app.models.user import User
from app.repositories.account_tokens import AccountTokenRepository
from app.repositories.documents import DocumentRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.services.email import EmailSender
from app.services.storage import StorageBackend, StorageError

logger = logging.getLogger(__name__)


def _is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


class AccountService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        email_sender: EmailSender,
        storage: StorageBackend,
    ) -> None:
        self.session = session
        self.settings = settings
        self.email_sender = email_sender
        self.storage = storage
        self.users = UserRepository(session)
        self.account_tokens = AccountTokenRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)
        self.documents = DocumentRepository(session)

    def _token_expiry(self, purpose: AccountTokenPurpose) -> datetime:
        if purpose == AccountTokenPurpose.EMAIL_VERIFICATION:
            return datetime.now(UTC) + timedelta(hours=self.settings.email_verification_token_hours)
        return datetime.now(UTC) + timedelta(minutes=self.settings.password_reset_token_minutes)

    async def _issue_token(self, user_id: uuid.UUID, purpose: AccountTokenPurpose) -> str:
        raw_token = secrets.token_urlsafe(32)
        self.account_tokens.add(
            AccountToken(
                user_id=user_id,
                purpose=purpose,
                token_hash=hash_token(raw_token),
                expires_at=self._token_expiry(purpose),
            )
        )
        await self.session.commit()
        return raw_token

    async def _send_best_effort(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        purpose: AccountTokenPurpose,
    ) -> None:
        try:
            await self.email_sender.send(to=to, subject=subject, text=text)
        except Exception:
            logger.exception(
                "Could not deliver account email",
                extra={"purpose": purpose.value, "email_to": to},
            )

    async def send_verification(self, user: User, *, force: bool = False) -> None:
        if user.email_verified_at is not None and not force:
            return
        await self.account_tokens.invalidate_unused(
            user.id, purpose=AccountTokenPurpose.EMAIL_VERIFICATION
        )
        raw_token = await self._issue_token(user.id, AccountTokenPurpose.EMAIL_VERIFICATION)
        link = f"{self.settings.frontend_base_url.rstrip('/')}/verify-email?token={raw_token}"
        await self._send_best_effort(
            to=user.email,
            subject="Verify your DocuMind AI email address",
            text=(
                "Confirm your email address for DocuMind AI by opening this link:\n\n"
                f"{link}\n\n"
                f"This link expires in {self.settings.email_verification_token_hours} hours."
            ),
            purpose=AccountTokenPurpose.EMAIL_VERIFICATION,
        )

    async def verify_email(self, raw_token: str) -> None:
        stored = await self.account_tokens.get_by_hash_for_update(
            hash_token(raw_token), purpose=AccountTokenPurpose.EMAIL_VERIFICATION
        )
        if (
            stored is None
            or stored.used_at is not None
            or _is_expired(stored.expires_at)
            or not await self.account_tokens.consume(stored.id)
        ):
            raise BadRequestError("This verification link is invalid or has expired")
        user = await self.users.get_by_id(stored.user_id)
        if user is None:
            raise BadRequestError("This verification link is invalid or has expired")
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
        await self.session.commit()

    async def request_password_reset(self, email: str) -> None:
        user = await self.users.get_by_email(email)
        if user is None or not user.is_active:
            return
        await self.account_tokens.invalidate_unused(
            user.id, purpose=AccountTokenPurpose.PASSWORD_RESET
        )
        raw_token = await self._issue_token(user.id, AccountTokenPurpose.PASSWORD_RESET)
        link = f"{self.settings.frontend_base_url.rstrip('/')}/reset-password?token={raw_token}"
        await self._send_best_effort(
            to=user.email,
            subject="Reset your DocuMind AI password",
            text=(
                "Use this link to choose a new password for DocuMind AI:\n\n"
                f"{link}\n\n"
                f"This link expires in {self.settings.password_reset_token_minutes} minutes. "
                "If you did not request a reset, you can ignore this message."
            ),
            purpose=AccountTokenPurpose.PASSWORD_RESET,
        )

    async def reset_password(self, raw_token: str, new_password: str) -> None:
        stored = await self.account_tokens.get_by_hash_for_update(
            hash_token(raw_token), purpose=AccountTokenPurpose.PASSWORD_RESET
        )
        if (
            stored is None
            or stored.used_at is not None
            or _is_expired(stored.expires_at)
            or not await self.account_tokens.consume(stored.id)
        ):
            raise BadRequestError("This password reset link is invalid or has expired")
        user = await self.users.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise BadRequestError("This password reset link is invalid or has expired")
        if verify_password(new_password, user.password_hash):
            await self.session.commit()
            raise ConflictError("New password must differ from the current password")
        user.password_hash = hash_password(new_password)
        await self.refresh_tokens.revoke_all_for_user(user.id, reason="password_reset")
        await self.session.commit()

    async def delete_account(self, user: User, *, password: str) -> None:
        if not verify_password(password, user.password_hash):
            raise UnauthorizedError("Password is incorrect")
        storage_keys = await self.documents.list_storage_keys_for_user(user.id)
        await self.refresh_tokens.revoke_all_for_user(user.id, reason="account_deleted")
        await self.session.delete(user)
        await self.session.commit()
        for storage_key in storage_keys:
            try:
                await self.storage.delete(storage_key)
            except StorageError:
                logger.warning("Could not remove stored file during account deletion")

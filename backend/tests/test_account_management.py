from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_token
from app.db.session import AsyncSessionLocal
from app.models.account_token import AccountToken
from app.models.enums import AccountTokenPurpose
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.workers.tasks import _cleanup_expired_tokens
from tests.conftest import TEST_UPLOAD_PATH, settings
from tests.fakes import register_user, upload_pdf


def _pdf_files() -> list[Path]:
    return list(Path(TEST_UPLOAD_PATH).rglob("*.pdf"))


async def _user(email: str = "ada@example.com") -> User:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        return user


async def test_registration_sends_single_use_verification_email(
    client: AsyncClient, email_sender
) -> None:
    user = await register_user(client)
    assert user["email_verified_at"] is None
    assert len(email_sender.messages) == 1

    token = email_sender.latest_token("email_verification")
    async with AsyncSessionLocal() as session:
        stored = (
            await session.execute(
                select(AccountToken).where(
                    AccountToken.purpose == AccountTokenPurpose.EMAIL_VERIFICATION
                )
            )
        ).scalar_one()
    assert stored.token_hash == hash_token(token)
    assert token not in stored.token_hash

    verified = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    assert verified.json()["message"] == "Email verified"

    profile = await client.get("/api/v1/auth/me")
    assert profile.json()["email_verified_at"] is not None

    replay = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "bad_request"


async def test_resend_verification_invalidates_previous_token(
    client: AsyncClient, email_sender
) -> None:
    await register_user(client)
    first = email_sender.latest_token("email_verification")
    resent = await client.post("/api/v1/auth/resend-verification")
    assert resent.status_code == 200
    second = email_sender.latest_token("email_verification")
    assert second != first

    stale = await client.post("/api/v1/auth/verify-email", json={"token": first})
    assert stale.status_code == 400
    current = await client.post("/api/v1/auth/verify-email", json={"token": second})
    assert current.status_code == 200


async def test_login_can_require_verified_email(
    client: AsyncClient, email_sender, monkeypatch
) -> None:
    await register_user(client)
    await client.post("/api/v1/auth/logout")
    monkeypatch.setattr(settings, "email_verification_required", True)

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "email_verification_required"
    assert client.cookies.get("documind_access") is None

    token = email_sender.latest_token("email_verification")
    verified = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    allowed = await client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert allowed.status_code == 200


async def test_password_reset_is_enumeration_safe_and_single_use(
    client: AsyncClient, email_sender
) -> None:
    await register_user(client)
    await client.post("/api/v1/auth/logout")
    email_sender.messages.clear()

    unknown = await client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "nobody@example.com"},
    )
    known = await client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "ada@example.com"},
    )
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()
    assert len(email_sender.messages) == 1
    assert email_sender.messages[0]["to"] == "ada@example.com"

    token = email_sender.latest_token("password_reset")
    rejected = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "WeakPassword"},
    )
    assert rejected.status_code == 422

    confirmed = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "NewSecure456"},
    )
    assert confirmed.status_code == 204, confirmed.text

    replay = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "AnotherPass789"},
    )
    assert replay.status_code == 400

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "NewSecure456"},
    )
    assert new_login.status_code == 200


async def test_password_reset_revokes_existing_sessions(client_factory, email_sender) -> None:
    first = await client_factory()
    await register_user(first)
    old_refresh = first.cookies.get("documind_refresh")
    assert old_refresh

    await first.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "ada@example.com"},
    )
    token = email_sender.latest_token("password_reset")
    assert (
        await first.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": "NewSecure456"},
        )
    ).status_code == 204

    replay_client = await client_factory()
    replay = await replay_client.post(
        "/api/v1/auth/refresh",
        headers={"Cookie": f"documind_refresh={old_refresh}"},
    )
    assert replay.status_code == 401


async def test_account_deletion_requires_password_and_removes_owned_data(
    client: AsyncClient,
) -> None:
    await register_user(client)
    document = (await upload_pdf(client)).json()
    stored_files = await asyncio.to_thread(_pdf_files)
    assert stored_files

    denied = await client.post(
        "/api/v1/auth/delete-account",
        json={"password": "WrongPass123"},
    )
    assert denied.status_code == 401
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    deleted = await client.post(
        "/api/v1/auth/delete-account",
        json={"password": "SecurePass123"},
    )
    assert deleted.status_code == 204, deleted.text
    assert client.cookies.get("documind_access") is None
    assert not await asyncio.to_thread(_pdf_files)

    assert (await client.get("/api/v1/auth/me")).status_code == 401
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert login.status_code == 401
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        tokens = (await session.execute(select(RefreshToken))).scalars().all()
    assert users == []
    assert tokens == []
    assert document["id"]


async def test_token_cleanup_removes_stale_records(client: AsyncClient) -> None:
    user = await register_user(client)
    user_id = uuid.UUID(user["id"])
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AccountToken(
                    user_id=user_id,
                    purpose=AccountTokenPurpose.PASSWORD_RESET,
                    token_hash=hash_token("stale-expired"),
                    expires_at=now - timedelta(days=60),
                ),
                AccountToken(
                    user_id=user_id,
                    purpose=AccountTokenPurpose.EMAIL_VERIFICATION,
                    token_hash=hash_token("recent"),
                    expires_at=now + timedelta(days=1),
                ),
                RefreshToken(
                    user_id=user_id,
                    family_id=uuid.uuid4(),
                    token_hash=hash_token("stale-refresh"),
                    expires_at=now - timedelta(days=60),
                ),
            ]
        )
        await session.commit()

    account_deleted, refresh_deleted = await _cleanup_expired_tokens()
    assert account_deleted >= 1
    assert refresh_deleted >= 1

    async with AsyncSessionLocal() as session:
        remaining = (
            await session.execute(
                select(AccountToken).where(AccountToken.token_hash == hash_token("recent"))
            )
        ).scalar_one_or_none()
    assert remaining is not None

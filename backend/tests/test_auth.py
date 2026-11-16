from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.security import hash_token
from app.db.session import AsyncSessionLocal
from app.models.refresh_token import RefreshToken
from tests.conftest import settings
from tests.fakes import register_user


def _set_cookie_headers(response: object) -> list[str]:
    return response.headers.get_list("set-cookie")  # type: ignore[attr-defined]


def _signed_refresh_token(user_id: str, expires_at: datetime) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": expires_at,
            "iss": settings.jwt_issuer,
        },
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


async def _store_refresh_token(
    user_id: str,
    token: str,
    *,
    expires_at: datetime,
    revoked_reason: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            RefreshToken(
                user_id=uuid.UUID(user_id),
                family_id=uuid.uuid4(),
                token_hash=hash_token(token),
                expires_at=expires_at,
                revoked_at=datetime.now(UTC) if revoked_reason else None,
                revoked_reason=revoked_reason,
            )
        )
        await session.commit()


def _cookie(refresh_token: str) -> dict[str, str]:
    return {"Cookie": f"documind_refresh={refresh_token}"}


async def _move_revocation_outside_grace(refresh_token: str, *, seconds: int = 90) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(refresh_token))
            .values(revoked_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )
        assert result.rowcount == 1
        await session.commit()


async def test_register_sets_secure_httponly_cookies(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ADA@Example.com",
            "password": "SecurePass123",
            "full_name": "  Ada   Lovelace  ",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["full_name"] == "Ada Lovelace"
    assert "password" not in response.text.lower()
    assert client.cookies.get("documind_access")
    assert client.cookies.get("documind_refresh")

    cookies = _set_cookie_headers(response)
    access_cookie = next(item for item in cookies if item.startswith("documind_access="))
    refresh_cookie = next(item for item in cookies if item.startswith("documind_refresh="))
    for header in (access_cookie, refresh_cookie):
        assert "HttpOnly" in header
        assert "Path=/" in header
        assert "SameSite=lax" in header
    assert "refresh" not in body


async def test_duplicate_registration_and_weak_passwords(client: AsyncClient) -> None:
    await register_user(client)
    duplicate = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ada@example.com",
            "password": "SecurePass123",
            "full_name": "Another Ada",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    for password in ("onlyletters", "12345678", "short1"):
        invalid = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"{password}@example.com",
                "password": password,
                "full_name": "Weak Password",
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"

    invalid_email = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "not-an-email",
            "password": "SecurePass123",
            "full_name": "Invalid Email",
        },
    )
    assert invalid_email.status_code == 422


async def test_login_logout_and_profile(client: AsyncClient) -> None:
    await register_user(client)
    await client.post("/api/v1/auth/logout")
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "WrongPass123"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["message"] == "Invalid email or password"

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "ADA@example.com", "password": "SecurePass123"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "ada@example.com"

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"

    updated = await client.patch("/api/v1/auth/me", json={"full_name": "  Ada   Byron  "})
    assert updated.status_code == 200
    assert updated.json()["full_name"] == "Ada Byron"

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_bearer_token_support(client: AsyncClient) -> None:
    await register_user(client)
    token = client.cookies.get("documind_access")
    assert token
    client.cookies.clear()
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


async def test_refresh_rotation_and_reuse_detection(
    client_factory,
) -> None:
    client = await client_factory()
    await register_user(client)
    old_refresh = client.cookies.get("documind_refresh")
    old_access = client.cookies.get("documind_access")
    assert old_refresh and old_access

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    new_refresh = client.cookies.get("documind_refresh")
    new_access = client.cookies.get("documind_access")
    assert new_refresh and new_refresh != old_refresh
    assert new_access and new_access != old_access

    await _move_revocation_outside_grace(old_refresh)

    reuse_client = await client_factory()
    reuse = await reuse_client.post(
        "/api/v1/auth/refresh",
        headers={"Cookie": f"documind_refresh={old_refresh}"},
    )
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "unauthorized"

    revoked_chain = await client.post("/api/v1/auth/refresh")
    assert revoked_chain.status_code == 401


async def test_change_password_revokes_sessions(client: AsyncClient) -> None:
    await register_user(client)
    old_refresh = client.cookies.get("documind_refresh")

    wrong = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "WrongPass123", "new_password": "NewSecure456"},
    )
    assert wrong.status_code == 401

    changed = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "SecurePass123", "new_password": "NewSecure456"},
    )
    assert changed.status_code == 200, changed.text
    assert client.cookies.get("documind_refresh") != old_refresh

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


async def test_refresh_requires_cookie(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["request_id"]


async def test_rotation_keeps_the_session_family(client_factory) -> None:
    client = await client_factory()
    user = await register_user(client)
    old_refresh = client.cookies.get("documind_refresh")
    assert old_refresh

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    new_refresh = client.cookies.get("documind_refresh")
    assert new_refresh and new_refresh != old_refresh

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash.in_([hash_token(old_refresh), hash_token(new_refresh)])
            )
        )
        tokens = list(result.scalars().all())

    assert len(tokens) == 2
    assert tokens[0].family_id == tokens[1].family_id
    old_row = next(row for row in tokens if row.token_hash == hash_token(old_refresh))
    new_row = next(row for row in tokens if row.token_hash == hash_token(new_refresh))
    assert old_row.revoked_reason == "rotated"
    assert old_row.revoked_at is not None
    assert new_row.revoked_at is None
    assert new_row.user_id == uuid.UUID(user["id"])


async def test_expired_refresh_token_does_not_revoke_other_sessions(
    client_factory,
) -> None:
    first = await client_factory()
    second = await client_factory()
    user = await register_user(first)
    login = await second.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert login.status_code == 200

    expired_token = _signed_refresh_token(user["id"], datetime.now(UTC) - timedelta(minutes=5))
    await _store_refresh_token(
        user["id"],
        expired_token,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    expired = await first.post(
        "/api/v1/auth/refresh",
        headers=_cookie(expired_token),
    )
    assert expired.status_code == 401
    assert "expired" in expired.json()["error"]["message"].lower()

    assert (await second.post("/api/v1/auth/refresh")).status_code == 200


async def test_unknown_refresh_token_does_not_revoke_all_sessions(
    client_factory,
) -> None:
    first = await client_factory()
    second = await client_factory()
    user = await register_user(first)
    login = await second.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert login.status_code == 200

    unknown_token = _signed_refresh_token(user["id"], datetime.now(UTC) + timedelta(days=1))
    response = await first.post("/api/v1/auth/refresh", headers=_cookie(unknown_token))
    assert response.status_code == 401
    assert "invalid" in response.json()["error"]["message"].lower()

    assert (await second.post("/api/v1/auth/refresh")).status_code == 200


async def test_reuse_only_revokes_the_replayed_session_family(client_factory) -> None:
    first = await client_factory()
    second = await client_factory()
    await register_user(first)
    old_refresh = first.cookies.get("documind_refresh")
    assert old_refresh

    login = await second.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "SecurePass123"},
    )
    assert login.status_code == 200

    rotated = await first.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    new_refresh = first.cookies.get("documind_refresh")
    assert new_refresh and new_refresh != old_refresh

    await _move_revocation_outside_grace(old_refresh)

    replay_client = await client_factory()
    replay = await replay_client.post(
        "/api/v1/auth/refresh",
        headers=_cookie(old_refresh),
    )
    assert replay.status_code == 401
    assert "reused" in replay.json()["error"]["message"].lower()

    assert (await first.post("/api/v1/auth/refresh")).status_code == 401
    assert (await second.post("/api/v1/auth/refresh")).status_code == 200


async def test_replay_within_rotation_grace_keeps_the_new_session(
    client_factory,
) -> None:
    first = await client_factory()
    await register_user(first)
    old_refresh = first.cookies.get("documind_refresh")
    assert old_refresh

    rotated = await first.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    new_refresh = first.cookies.get("documind_refresh")
    assert new_refresh and new_refresh != old_refresh

    replay_client = await client_factory()
    replay = await replay_client.post(
        "/api/v1/auth/refresh",
        headers=_cookie(old_refresh),
    )
    assert replay.status_code == 401
    assert "retry" in replay.json()["error"]["message"].lower()

    assert (await first.post("/api/v1/auth/refresh")).status_code == 200


async def test_concurrent_refresh_requests_have_a_single_winner(client_factory) -> None:
    import asyncio

    seed = await client_factory()
    await register_user(seed)
    shared_refresh = seed.cookies.get("documind_refresh")
    assert shared_refresh

    racers = [await client_factory(), await client_factory()]
    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/refresh", headers=_cookie(shared_refresh)) for client in racers)
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 401]
    assert all(
        response.json()["error"]["code"] == "unauthorized"
        for response in responses
        if response.status_code == 401
    )

    winner = next(response for response in responses if response.status_code == 200)
    winner_client = racers[responses.index(winner)]
    assert (await winner_client.post("/api/v1/auth/refresh")).status_code == 200


async def test_logout_only_revokes_the_current_session(client_factory) -> None:
    first = await client_factory()
    second = await client_factory()
    await register_user(first)
    old_refresh = first.cookies.get("documind_refresh")
    assert old_refresh
    assert (
        await second.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "SecurePass123"},
        )
    ).status_code == 200

    assert (await first.post("/api/v1/auth/logout")).status_code == 204

    replay_client = await client_factory()
    replay = await replay_client.post(
        "/api/v1/auth/refresh",
        headers=_cookie(old_refresh),
    )
    assert replay.status_code == 401
    assert "revoked" in replay.json()["error"]["message"].lower()

    assert (await second.post("/api/v1/auth/refresh")).status_code == 200

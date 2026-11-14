from __future__ import annotations

from httpx import AsyncClient

from tests.fakes import register_user


def _set_cookie_headers(response: object) -> list[str]:
    return response.headers.get_list("set-cookie")  # type: ignore[attr-defined]


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

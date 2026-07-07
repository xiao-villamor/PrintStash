"""E2E: first-run setup, login, token refresh, API keys, and RBAC.

Drives the real auth surface end to end against the live app: the first-run
wizard creates the only superuser, login issues a JWT, refresh rotates it, an API
key can be exchanged for a JWT, and a non-superuser is denied an admin-only route.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_setup_login_refresh_apikey_and_rbac(api, tmp_path, e2e_db):
    # Fresh instance: setup is required, app is unconfigured.
    status = (await api.get("/api/v1/setup/status")).json()
    assert status["configured"] is False
    assert status["user_count"] == 0

    # Complete first-run setup -> creates the first superuser, returns a JWT.
    data_dir = tmp_path / "files"
    thumb_dir = tmp_path / "thumbs"
    r = await api.post(
        "/api/v1/setup",
        json={
            "username": "owner",
            "password": "Password123",
            "storage_backend": "local",
            "data_dir": str(data_dir),
            "thumb_dir": str(thumb_dir),
        },
    )
    assert r.status_code == 201, r.text
    setup_token = r.json()["access_token"]

    # The wizard refuses to run twice.
    again = await api.post(
        "/api/v1/setup",
        json={"username": "owner2", "password": "Password123"},
    )
    assert again.status_code == 409

    # The JWT from setup authenticates /me as the superuser.
    me = await api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {setup_token}"})
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "owner"
    assert me.json()["is_superuser"] is True

    # Login with username + password issues an access + refresh token.
    login = await api.post(
        "/api/v1/auth/login", json={"username": "owner", "password": "Password123"}
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert tokens["scope"] == "admin"
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    # Wrong password is rejected.
    bad = await api.post(
        "/api/v1/auth/login", json={"username": "owner", "password": "wrong-password"}
    )
    assert bad.status_code == 401

    # Refresh rotates to a working access token.
    refreshed = await api.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 200, refreshed.text
    new_access = refreshed.json()["access_token"]
    me2 = await api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me2.status_code == 200

    # Create an API key and exchange it for a JWT via login.
    admin_headers = {"Authorization": f"Bearer {access}"}
    key = await api.post("/api/v1/auth/api-keys", json={"name": "ci"}, headers=admin_headers)
    assert key.status_code == 200, key.text
    raw_key = key.json()["api_key"]
    key_login = await api.post(
        "/api/v1/auth/login", json={"username": "owner", "api_key": raw_key}
    )
    assert key_login.status_code == 200, key_login.text
    assert key_login.json()["scope"] == "admin"


@pytest.mark.asyncio
async def test_non_superuser_is_denied_admin_routes(api, e2e_db):
    from app.db.models import User
    from app.services.auth import create_access_token, hash_password

    # Seed a plain (non-superuser) writer.
    user = User(
        username="writer",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    e2e_db.add(user)
    e2e_db.commit()
    e2e_db.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    headers = {"Authorization": f"Bearer {token}"}

    # /me works for any authenticated user...
    assert (await api.get("/api/v1/auth/me", headers=headers)).status_code == 200
    # ...but the notifications master switch is superuser-only.
    denied = await api.get("/api/v1/notifications", headers=headers)
    assert denied.status_code == 403
    # And no credentials at all is a 401.
    assert (await api.get("/api/v1/notifications")).status_code == 401

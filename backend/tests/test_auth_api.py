from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import User
from app.services.auth import create_api_key, hash_password


def _create_user(
    session: Session,
    username: str,
    password: str,
    *,
    is_superuser: bool,
) -> User:
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_superuser=is_superuser,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


class TestAuthFlow:
    def test_login_returns_access_and_refresh(
        self, client: TestClient, db_session: Session
    ):
        _create_user(db_session, "alice", "Password123", is_superuser=False)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "Password123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["scope"] == "write"
        assert isinstance(body["access_token"], str) and body["access_token"]
        assert isinstance(body["refresh_token"], str) and body["refresh_token"]

    def test_login_accepts_username_and_api_key(
        self, client: TestClient, db_session: Session
    ):
        user = _create_user(
            db_session, "script-user", "Password123", is_superuser=False
        )
        _, raw_key = create_api_key(db_session, user.id, "Orca uploader")

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "script-user", "api_key": raw_key},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["scope"] == "write"
        assert isinstance(body["access_token"], str) and body["access_token"]

    def test_login_rejects_password_and_api_key_together(
        self, client: TestClient, db_session: Session
    ):
        user = _create_user(db_session, "dual-user", "Password123", is_superuser=False)
        _, raw_key = create_api_key(db_session, user.id, "Bad client")

        resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": "dual-user",
                "password": "Password123",
                "api_key": raw_key,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "provide_password_or_api_key"

    def test_api_key_is_not_a_bearer_token(
        self, client: TestClient, db_session: Session
    ):
        user = _create_user(db_session, "direct-key", "Password123", is_superuser=False)
        _, raw_key = create_api_key(db_session, user.id, "Direct header")

        resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "not_authenticated"

    def test_user_can_create_and_revoke_api_key(
        self, client: TestClient, db_session: Session
    ):
        _create_user(db_session, "key-owner", "Password123", is_superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "key-owner", "password": "Password123"},
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        created = client.post(
            "/api/v1/auth/api-keys",
            json={"name": "Uploader"},
            headers=headers,
        )
        assert created.status_code == 200
        key_body = created.json()
        assert key_body["name"] == "Uploader"
        assert key_body["api_key"].startswith("psk_")

        listed = client.get("/api/v1/auth/api-keys", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == key_body["id"]

        deleted = client.delete(
            f"/api/v1/auth/api-keys/{key_body['id']}",
            headers=headers,
        )
        assert deleted.status_code == 204

        relogin = client.post(
            "/api/v1/auth/login",
            json={"username": "key-owner", "api_key": key_body["api_key"]},
        )
        assert relogin.status_code == 401

    def test_refresh_rotates_token(self, client: TestClient, db_session: Session):
        _create_user(db_session, "bob", "Password123", is_superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "Password123"},
        ).json()

        refreshed = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": login["refresh_token"]},
        )
        assert refreshed.status_code == 200
        new_body = refreshed.json()
        assert new_body["refresh_token"] != login["refresh_token"]

        second_use = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": login["refresh_token"]},
        )
        assert second_use.status_code == 401
        assert second_use.json()["detail"] == "invalid_refresh_token"

    def test_logout_revokes_access_and_refresh(
        self, client: TestClient, db_session: Session
    ):
        _create_user(db_session, "carol", "Password123", is_superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "carol", "password": "Password123"},
        ).json()
        access = login["access_token"]
        refresh = login["refresh_token"]

        logout = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert logout.status_code == 200
        assert logout.json()["ok"] is True

        me = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert me.status_code == 401
        assert me.json()["detail"] == "not_authenticated"

        refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert refreshed.status_code == 401
        assert refreshed.json()["detail"] == "invalid_refresh_token"


class TestAdminEnforcement:
    def test_config_requires_superuser(self, client: TestClient, db_session: Session):
        _create_user(db_session, "dave", "Password123", is_superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "dave", "password": "Password123"},
        ).json()
        resp = client.get(
            "/api/v1/config",
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "admin_required"

    def test_config_allows_superuser(self, client: TestClient, db_session: Session):
        _create_user(db_session, "erin", "Password123", is_superuser=True)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "erin", "password": "Password123"},
        ).json()
        resp = client.get(
            "/api/v1/config",
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
        assert resp.status_code == 200

    def test_backups_require_superuser(self, client: TestClient, db_session: Session):
        _create_user(db_session, "frank", "Password123", is_superuser=False)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "frank", "password": "Password123"},
        ).json()
        resp = client.get(
            "/api/v1/backups",
            headers={"Authorization": f"Bearer {login['access_token']}"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "admin_required"


class TestAdminUserManagement:
    def test_superuser_can_create_update_reset_and_disable_user(
        self, client: TestClient, db_session: Session
    ):
        _create_user(db_session, "admin", "Password123", is_superuser=True)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Password123"},
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        created = client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={
                "username": "new-user",
                "password": "Password123",
                "email": "new@example.com",
            },
        )
        assert created.status_code == 201
        user_id = created.json()["id"]

        updated = client.patch(
            f"/api/v1/admin/users/{user_id}",
            headers=headers,
            json={"is_superuser": True},
        )
        assert updated.status_code == 200
        assert updated.json()["is_superuser"] is True

        reset = client.post(
            f"/api/v1/admin/users/{user_id}/password",
            headers=headers,
            json={"password": "NewPassword123"},
        )
        assert reset.status_code == 200

        relogin = client.post(
            "/api/v1/auth/login",
            json={"username": "new-user", "password": "NewPassword123"},
        )
        assert relogin.status_code == 200

        disabled = client.delete(f"/api/v1/admin/users/{user_id}", headers=headers)
        assert disabled.status_code == 204

        denied = client.post(
            "/api/v1/auth/login",
            json={"username": "new-user", "password": "NewPassword123"},
        )
        assert denied.status_code == 401

    def test_cannot_disable_last_active_superuser(
        self, client: TestClient, db_session: Session
    ):
        admin = _create_user(db_session, "solo-admin", "Password123", is_superuser=True)
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "solo-admin", "password": "Password123"},
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        response = client.patch(
            f"/api/v1/admin/users/{admin.id}",
            headers=headers,
            json={"is_superuser": False},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "last_superuser_required"

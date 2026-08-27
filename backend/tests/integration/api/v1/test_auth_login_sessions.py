"""Login, refresh, logout, and current-user sessions remain durable and bounded."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import RefreshToken
from app.services.auth import create_api_key

from ._auth_shared import create_user, headers, login


class TestLogin:
    def test_logs_in_a_local_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "local-login")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "local-login", "password": "Password123"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["access_token"]
        assert response.json()["token_type"] == "bearer"

    def test_issues_a_refresh_token_for_local_login(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "refresh-login")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": "Password123"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["refresh_token"]
        token_rows = db_session.exec(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        ).all()
        assert len(token_rows) == 1

    def test_authenticates_a_named_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "named-key-login")
        _, raw_key = create_api_key(db_session, user.id, "Slicer hook")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "api_key": raw_key},
        )

        assert response.status_code == 200, response.text
        assert response.json()["scope"] == "write"

    def test_applies_remember_me_session_lifetime(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "remembered-login")

        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": "remembered-login",
                "password": "Password123",
                "remember_me": True,
            },
        )

        assert response.status_code == 200, response.text
        assert "Max-Age=" in response.headers["set-cookie"]

    def test_uses_the_standard_session_lifetime_by_default(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "standard-login")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "standard-login", "password": "Password123"},
        )

        assert response.status_code == 200, response.text
        assert "Max-Age=" not in response.headers["set-cookie"]

    def test_rejects_an_incorrect_password_without_user_disclosure(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "wrong-password")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "wrong-password", "password": "WrongPassword123"},
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "invalid_credentials"}

    def test_rejects_an_unknown_username_without_user_disclosure(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "unknown-user", "password": "WrongPassword123"},
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "invalid_credentials"}

    def test_rejects_login_for_an_inactive_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "inactive-login", is_active=False)

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "inactive-login", "password": "Password123"},
        )

        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "invalid_credentials"}

    def test_rejects_a_revoked_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "revoked-key-login")
        created = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(user),
            json={"name": "Revoked key"},
        ).json()
        deleted = client.delete(
            f"/api/v1/auth/api-keys/{created['id']}", headers=headers(user)
        )
        assert deleted.status_code == 204, deleted.text

        response = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "api_key": created["api_key"]},
        )

        assert response.status_code == 401, response.text

    def test_rejects_an_api_key_owned_by_an_inactive_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "inactive-key-owner")
        _, raw_key = create_api_key(db_session, user.id, "Inactive owner")
        user.is_active = False
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "api_key": raw_key},
        )

        assert response.status_code == 401, response.text

    def test_rejects_login_without_a_password_or_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "credential-less-login")

        response = client.post(
            "/api/v1/auth/login", json={"username": "credential-less-login"}
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "provide_password_or_api_key"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {"username": "", "password": "Password123"},
                id="empty-username",
            ),
            pytest.param(
                {"username": "u" * 129, "password": "Password123"},
                id="long-username",
            ),
            pytest.param(
                {"username": "boundary-login", "password": ""},
                id="empty-password",
            ),
            pytest.param(
                {"username": "boundary-login", "password": "p" * 257},
                id="long-password",
            ),
            pytest.param(
                {"username": "boundary-login", "api_key": "k" * 257},
                id="long-api-key",
            ),
        ],
    )
    def test_validates_login_field_boundaries(
        self, client: TestClient, payload: dict[str, str]
    ) -> None:
        response = client.post("/api/v1/auth/login", json=payload)

        assert response.status_code == 422, response.text


class TestRefresh:
    def test_refreshes_an_active_session(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "active-refresh")
        original = login(client, "active-refresh")

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["access_token"]
        assert response.json()["refresh_token"]

    def test_rotates_a_refresh_token_atomically(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "atomic-refresh")
        original = login(client, user.username)

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )

        assert response.status_code == 200, response.text
        rows = db_session.exec(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        ).all()
        assert sum(not row.revoked for row in rows) == 1
        assert sum(row.revoked for row in rows) == 1

    def test_rejects_replay_of_a_rotated_refresh_token(
        self, client: TestClient, db_session: Session
    ) -> None:
        create_user(db_session, "replayed-refresh")
        original = login(client, "replayed-refresh")
        first = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )
        assert first.status_code == 200, first.text

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )

        assert response.status_code == 401, response.text

    def test_rejects_an_invalid_refresh_token(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "invalid-token"}
        )

        assert response.status_code == 401, response.text

    def test_rejects_an_expired_refresh_token(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "expired-refresh")
        token = login(client, user.username)["refresh_token"]
        row = db_session.exec(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        ).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401, response.text

    def test_rejects_a_revoked_refresh_token(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "revoked-refresh")
        token = login(client, user.username)["refresh_token"]
        row = db_session.exec(
            select(RefreshToken).where(RefreshToken.user_id == user.id)
        ).one()
        row.revoked = True
        db_session.add(row)
        db_session.commit()

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401, response.text

    def test_rejects_refresh_for_an_inactive_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "inactive-refresh")
        token = login(client, user.username)["refresh_token"]
        user.is_active = False
        db_session.add(user)
        db_session.commit()

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("", id="empty"),
            pytest.param("x" * 513, id="long"),
        ],
    )
    def test_validates_refresh_token_boundaries(
        self, client: TestClient, token: str
    ) -> None:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 422, response.text

    def test_rate_limits_repeated_refresh_failures(self, client: TestClient) -> None:
        for attempt in range(10):
            response = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": f"invalid-token-{attempt}"},
            )
            assert response.status_code == 401, response.text

        blocked = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "blocked-token"}
        )

        assert blocked.status_code == 429, blocked.text


class TestLogout:
    def test_denies_unauthenticated_logout(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/logout")

        assert response.status_code == 401, response.text


class TestGetMe:
    def test_returns_the_current_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "current-user")

        response = client.get("/api/v1/auth/me", headers=headers(user))

        assert response.status_code == 200, response.text
        assert response.json()["id"] == user.id
        assert response.json()["username"] == user.username

    def test_rejects_a_revoked_access_session_on_current_user_lookup(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = create_user(db_session, "revoked-current-user")
        session = login(client, user.username)
        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )
        assert logout.status_code == 200, logout.text

        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )

        assert response.status_code == 401, response.text

    def test_denies_unauthenticated_current_user_lookup(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/auth/me")

        assert response.status_code == 401, response.text

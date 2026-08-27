"""User administration preserves identity, authorization, and session invariants."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import User

from ._admin_shared import _headers, _user


class TestListUsers:
    def test_lists_users_for_a_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "list-admin")
        target = _user(db_session, "listed-user", superuser=False)

        response = client.get("/api/v1/admin/users", headers=_headers(admin))

        assert response.status_code == 200, response.text
        assert [row["username"] for row in response.json()] == [
            admin.username,
            target.username,
        ]

    def test_omits_password_material_from_listed_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "password-list-admin")
        _user(db_session, "password-list-target", superuser=False)

        response = client.get("/api/v1/admin/users", headers=_headers(admin))

        assert response.status_code == 200, response.text
        assert all(
            "password" not in key and "hash" not in key
            for row in response.json()
            for key in row
        )

    def test_denies_an_unauthenticated_caller_from_listing_users(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/admin/users")

        assert response.status_code == 401, response.text


class TestCreateUser:
    def test_persists_a_created_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "persist-admin")

        response = client.post(
            "/api/v1/admin/users",
            headers=_headers(admin),
            json={"username": "persisted-user", "password": "Password123"},
        )

        assert response.status_code == 201, response.text
        user = db_session.exec(
            select(User).where(User.username == "persisted-user")
        ).one()
        assert user.hashed_password != "Password123"

    def test_creates_a_user_without_an_email(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "email-admin")

        response = client.post(
            "/api/v1/admin/users",
            headers=_headers(admin),
            json={"username": "email-optional", "password": "Password123"},
        )

        assert response.status_code == 201, response.text
        assert response.json()["email"] is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("username", "ab", id="short-username"),
            pytest.param("username", "u" * 129, id="long-username"),
            pytest.param("password", "short", id="short-password"),
            pytest.param("password", "p" * 257, id="long-password"),
        ],
    )
    def test_validates_user_creation_boundaries(
        self,
        client: TestClient,
        db_session: Session,
        field: str,
        value: str,
    ) -> None:
        admin = _user(db_session, f"boundary-admin-{field}-{len(value)}")
        payload = {"username": "boundary-user", "password": "Password123"}
        payload[field] = value

        response = client.post(
            "/api/v1/admin/users", headers=_headers(admin), json=payload
        )

        assert response.status_code == 422, response.text
        assert (
            db_session.exec(
                select(User).where(User.username == payload["username"])
            ).first()
            is None
        )

    def test_denies_a_non_superuser_from_creating_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-creator", superuser=False)

        response = client.post(
            "/api/v1/admin/users",
            headers=_headers(caller),
            json={"username": "must-not-exist", "password": "Password123"},
        )

        assert response.status_code == 403, response.text
        assert (
            db_session.exec(
                select(User).where(User.username == "must-not-exist")
            ).first()
            is None
        )

    def test_denies_an_unauthenticated_caller_from_creating_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = client.post(
            "/api/v1/admin/users",
            json={"username": "anonymous-create", "password": "Password123"},
        )

        assert response.status_code == 401, response.text
        assert (
            db_session.exec(
                select(User).where(User.username == "anonymous-create")
            ).first()
            is None
        )


class TestUpdateUser:
    def test_updates_a_users_email(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "email-update-admin")
        target = _user(db_session, "email-update-target", superuser=False)

        response = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_headers(admin),
            json={"email": "updated@example.test"},
        )

        assert response.status_code == 200, response.text
        db_session.refresh(target)
        assert target.email == "updated@example.test"

    def test_reenables_a_user(self, client: TestClient, db_session: Session) -> None:
        admin = _user(db_session, "reenable-admin")
        target = _user(db_session, "reenable-target", superuser=False, active=False)

        response = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_headers(admin),
            json={"is_active": True},
        )

        assert response.status_code == 200, response.text
        db_session.refresh(target)
        assert target.is_active is True

    def test_promotes_a_user_to_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "promote-admin")
        target = _user(db_session, "promote-target", superuser=False)

        response = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_headers(admin),
            json={"is_superuser": True},
        )

        assert response.status_code == 200, response.text
        db_session.refresh(target)
        assert target.is_superuser is True

    def test_accepts_an_empty_user_update(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "empty-update-admin")
        target = _user(db_session, "empty-update-target", superuser=False)
        original = (target.email, target.is_active, target.is_superuser)

        response = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_headers(admin),
            json={},
        )

        assert response.status_code == 200, response.text
        db_session.refresh(target)
        assert (target.email, target.is_active, target.is_superuser) == original

    def test_denies_a_non_superuser_from_updating_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-updater", superuser=False)
        target = _user(db_session, "update-protected", superuser=False)

        response = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_headers(caller),
            json={"email": "forbidden@example.test"},
        )

        assert response.status_code == 403, response.text
        db_session.refresh(target)
        assert target.email is None


class TestResetUserPassword:
    def test_resets_a_local_users_password(
        self, client: TestClient, db_session: Session
    ) -> None:
        admin = _user(db_session, "password-reset-admin")
        target = _user(db_session, "password-reset-target", superuser=False)

        response = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_headers(admin),
            json={"password": "NewPassword123"},
        )
        assert response.status_code == 200, response.text

        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": target.username,
                "password": "NewPassword123",
            },
        )

        assert login.status_code == 200, login.text

    @pytest.mark.parametrize(
        "password",
        [
            pytest.param("short", id="short"),
            pytest.param("p" * 257, id="long"),
        ],
    )
    def test_validates_password_reset_boundaries(
        self,
        client: TestClient,
        db_session: Session,
        password: str,
    ) -> None:
        admin = _user(db_session, f"password-boundary-admin-{len(password)}")
        target = _user(
            db_session,
            f"password-boundary-target-{len(password)}",
            superuser=False,
        )

        response = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_headers(admin),
            json={"password": password},
        )

        assert response.status_code == 422, response.text
        login = client.post(
            "/api/v1/auth/login",
            json={"username": target.username, "password": "Password123"},
        )
        assert login.status_code == 200, login.text

    def test_denies_a_non_superuser_from_resetting_passwords(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-password-reset", superuser=False)
        target = _user(db_session, "password-protected", superuser=False)

        response = client.post(
            f"/api/v1/admin/users/{target.id}/password",
            headers=_headers(caller),
            json={"password": "ForbiddenPassword123"},
        )

        assert response.status_code == 403, response.text
        login = client.post(
            "/api/v1/auth/login",
            json={"username": target.username, "password": "Password123"},
        )
        assert login.status_code == 200, login.text


class TestDeactivateUser:
    def test_denies_a_non_superuser_from_deactivating_users(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = _user(db_session, "regular-deactivator", superuser=False)
        target = _user(db_session, "deactivation-protected", superuser=False)

        response = client.delete(
            f"/api/v1/admin/users/{target.id}", headers=_headers(caller)
        )

        assert response.status_code == 403, response.text
        db_session.refresh(target)
        assert target.is_active is True

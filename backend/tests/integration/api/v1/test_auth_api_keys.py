"""Named API keys remain owner-scoped, one-time-visible, and revocable."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import ApiKey

from ._auth_shared import create_user, headers


class TestGetApiKeys:
    def test_lists_only_the_callers_api_keys(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = create_user(db_session, "key-list-owner")
        other = create_user(db_session, "key-list-other")
        own = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": "Own key"},
        ).json()
        client.post(
            "/api/v1/auth/api-keys",
            headers=headers(other),
            json={"name": "Other key"},
        )

        response = client.get("/api/v1/auth/api-keys", headers=headers(caller))

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [own["id"]]

    def test_omits_api_key_secrets_from_the_key_list(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = create_user(db_session, "key-list-secret-owner")
        created = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": "Hidden key"},
        ).json()

        response = client.get("/api/v1/auth/api-keys", headers=headers(caller))

        assert response.status_code == 200, response.text
        assert "api_key" not in response.json()[0]
        assert "key_hash" not in response.json()[0]
        assert created["api_key"] not in response.text

    def test_denies_unauthenticated_api_key_listing(self, client: TestClient) -> None:
        response = client.get("/api/v1/auth/api-keys")

        assert response.status_code == 401, response.text


class TestPostApiKey:
    def test_creates_a_named_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = create_user(db_session, "key-create-owner")

        response = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": "Slicer uploader"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Slicer uploader"
        assert response.json()["api_key"].startswith("psk_")

    def test_stores_only_a_hash_of_a_created_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = create_user(db_session, "key-hash-owner")

        response = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": "Hashed key"},
        )
        assert response.status_code == 200, response.text

        row = db_session.get(ApiKey, response.json()["id"])
        assert row is not None
        assert row.key_hash != response.json()["api_key"]
        assert response.json()["api_key"] not in row.key_hash

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("x", id="minimum"),
            pytest.param("x" * 128, id="maximum"),
        ],
    )
    def test_accepts_api_key_name_boundaries(
        self, client: TestClient, db_session: Session, name: str
    ) -> None:
        caller = create_user(db_session, f"key-boundary-owner-{len(name)}")

        response = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": name},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == name

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("x" * 129, id="over-maximum"),
        ],
    )
    def test_rejects_invalid_api_key_names(
        self, client: TestClient, db_session: Session, name: str
    ) -> None:
        caller = create_user(db_session, f"invalid-key-owner-{len(name)}")

        response = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": name},
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ApiKey)).all() == []

    def test_denies_unauthenticated_api_key_creation(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = client.post("/api/v1/auth/api-keys", json={"name": "Anonymous key"})

        assert response.status_code == 401, response.text
        assert db_session.exec(select(ApiKey)).all() == []


class TestDeleteApiKey:
    def test_revokes_an_owned_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = create_user(db_session, "key-revoke-owner")
        created = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(caller),
            json={"name": "Revoke me"},
        ).json()

        response = client.delete(
            f"/api/v1/auth/api-keys/{created['id']}", headers=headers(caller)
        )

        assert response.status_code == 204, response.text
        login = client.post(
            "/api/v1/auth/login",
            json={"username": caller.username, "api_key": created["api_key"]},
        )
        assert login.status_code == 401, login.text

    def test_returns_not_found_for_another_users_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        owner = create_user(db_session, "foreign-key-owner")
        caller = create_user(db_session, "foreign-key-caller")
        created = client.post(
            "/api/v1/auth/api-keys",
            headers=headers(owner),
            json={"name": "Owner key"},
        ).json()

        response = client.delete(
            f"/api/v1/auth/api-keys/{created['id']}", headers=headers(caller)
        )

        assert response.status_code == 404, response.text
        login = client.post(
            "/api/v1/auth/login",
            json={"username": owner.username, "api_key": created["api_key"]},
        )
        assert login.status_code == 200, login.text

    def test_returns_not_found_for_a_missing_api_key(
        self, client: TestClient, db_session: Session
    ) -> None:
        caller = create_user(db_session, "missing-key-owner")

        response = client.delete(
            "/api/v1/auth/api-keys/999999", headers=headers(caller)
        )

        assert response.status_code == 404, response.text

    def test_denies_unauthenticated_api_key_deletion(self, client: TestClient) -> None:
        response = client.delete("/api/v1/auth/api-keys/1")

        assert response.status_code == 401, response.text

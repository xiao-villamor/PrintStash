"""Connecting a capture provider to one account, and cutting it loose again.

A provider connection holds a live credential for somebody's paid account, so three
things are load-bearing here. It is **per user**: another account never sees it and never
disconnects it. The stored secret is **encrypted at rest** — the raw password must not be
readable in the table. And a credential is **validated before it replaces** the one
already stored, because silently overwriting a working login with a typo would break
imports with no way back.

Disconnecting also drops the owner's cached provider metadata: leaving it would keep
serving data fetched with a credential the user just revoked.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import CaptureProvider, ProviderConnection
from app.services import import_resolvers
from app.services import provider_connections as service
from app.services.capture_provider_connections import (
    ProviderConnectionError,
    ProviderModelMetadata,
)

CULTS_LOGIN = {"username": "fixture-user", "password": "fixture-password"}


class _AcceptingCults:
    async def validate_credentials(self, _candidate: object) -> None:
        return None


class _RejectingCults:
    async def validate_credentials(self, _candidate: object) -> None:
        raise ProviderConnectionError("provider_auth_failed")


@pytest.fixture
def cults_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the egress that checks a Cults login — the only mocked seam."""
    monkeypatch.setattr(
        service, "CultsMetadataClient", lambda _transport: _AcceptingCults()
    )


@pytest.fixture
def cults_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service, "CultsMetadataClient", lambda _transport: _RejectingCults()
    )


class TestListConnections:
    def test_reports_every_provider_as_disconnected_by_default(
        self, client: TestClient, user_headers
    ) -> None:
        body = client.get(
            "/api/v1/provider-connections", headers=user_headers("list-empty")
        ).json()

        assert [row["provider"] for row in body] == [p.value for p in CaptureProvider]
        assert all(row["connected"] is False for row in body)
        assert all(row["updated_at"] is None for row in body)

    def test_marks_a_connected_provider_with_the_time_it_was_linked(
        self, client: TestClient, user_headers, cults_accepts
    ) -> None:
        headers = user_headers("list-connected")
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json=CULTS_LOGIN,
        )

        body = client.get("/api/v1/provider-connections", headers=headers).json()

        cults = next(row for row in body if row["provider"] == "cults")
        assert cults["connected"] is True
        assert cults["updated_at"] is not None

    def test_hides_another_accounts_connection(
        self, client: TestClient, user_headers, cults_accepts
    ) -> None:
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("connection-owner"),
            json=CULTS_LOGIN,
        )

        body = client.get(
            "/api/v1/provider-connections", headers=user_headers("connection-stranger")
        ).json()

        assert all(row["connected"] is False for row in body)

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/provider-connections").status_code == 401


class TestConnectCults:
    def test_reports_the_provider_as_connected(
        self, client: TestClient, user_headers, cults_accepts
    ) -> None:
        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("cults-connect"),
            json=CULTS_LOGIN,
        )

        assert response.status_code == 200, response.text
        assert response.json()["provider"] == "cults"
        assert response.json()["connected"] is True

    def test_stores_the_password_encrypted_at_rest(
        self, client: TestClient, db_session: Session, user_headers, cults_accepts
    ) -> None:
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("cults-at-rest"),
            json={"username": "private-user", "password": "private-password"},
        )

        row = db_session.exec(select(ProviderConnection)).one()
        assert row.id is not None
        stored = (
            db_session.connection()
            .exec_driver_sql(
                "SELECT credential_secret FROM provider_connections WHERE id = ?",
                (row.id,),
            )
            .scalar_one()
        )
        assert "private-password" not in stored
        assert row.credential_secret == "private-user\nprivate-password"

    def test_rejects_a_login_the_provider_refuses(
        self, client: TestClient, user_headers, cults_rejects
    ) -> None:
        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("cults-refused"),
            json=CULTS_LOGIN,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "provider_connection_validation_failed"

    def test_stores_nothing_when_the_provider_refuses_the_login(
        self, client: TestClient, db_session: Session, user_headers, cults_rejects
    ) -> None:
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("cults-refused-empty"),
            json=CULTS_LOGIN,
        )

        assert db_session.exec(select(ProviderConnection)).all() == []

    def test_rejects_a_body_with_no_password(
        self, client: TestClient, user_headers, cults_accepts
    ) -> None:
        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("cults-no-password"),
            json={"username": "someone"},
        )

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/provider-connections/cults/connect", json=CULTS_LOGIN
        )

        assert response.status_code == 401, response.text


class TestDisconnect:
    def test_removes_the_connection(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        cults_accepts,
    ) -> None:
        headers = user_headers("cults-disconnect")
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json=CULTS_LOGIN,
        )

        response = client.delete(
            "/api/v1/provider-connections/cults/disconnect", headers=headers
        )

        assert response.status_code == 204, response.text
        assert db_session.exec(select(ProviderConnection)).all() == []

    def test_drops_the_owners_cached_provider_metadata(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        cults_accepts,
    ) -> None:
        user = make_user("cults-cache-drop")
        headers = headers_for(user)
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json=CULTS_LOGIN,
        )
        key = (user.id, "cults", "cached-model")
        import_resolvers._provider_metadata_cache[key] = (
            ProviderModelMetadata("cached-model", "stale", None, None, None),
            utcnow() + timedelta(minutes=5),
        )

        client.delete("/api/v1/provider-connections/cults/disconnect", headers=headers)

        # Serving metadata fetched with a credential the user just revoked is a leak.
        assert key not in import_resolvers._provider_metadata_cache

    def test_accepts_a_disconnect_of_a_provider_that_was_never_connected(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.delete(
            "/api/v1/provider-connections/cults/disconnect",
            headers=user_headers("never-connected"),
        )

        assert response.status_code == 204, response.text

    def test_leaves_another_accounts_connection_alone(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        cults_accepts,
    ) -> None:
        client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=user_headers("disconnect-owner"),
            json=CULTS_LOGIN,
        )

        client.delete(
            "/api/v1/provider-connections/cults/disconnect",
            headers=user_headers("disconnect-stranger"),
        )

        assert len(db_session.exec(select(ProviderConnection)).all()) == 1

    def test_rejects_a_provider_that_does_not_exist(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.delete(
            "/api/v1/provider-connections/thingiverse/disconnect",
            headers=user_headers("unknown-provider"),
        )

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        response = client.delete("/api/v1/provider-connections/cults/disconnect")

        assert response.status_code == 401, response.text

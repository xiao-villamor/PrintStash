"""Provider-connection HTTP responses stay user-scoped and secret-free.

The router is the last boundary before credentials enter or leave the vault;
these tests use the real database and replace only provider-network egress.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import CaptureProvider, ProviderConnection, ProviderOAuthState, User
from app.services import provider_connections as provider_service
from app.services.auth import create_access_token, hash_password


def _headers(
    session: Session, username: str, *, scope: str = "write"
) -> tuple[dict[str, str], User]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope=scope)
    return {"Authorization": f"Bearer {token}"}, user


def _connection(
    session: Session,
    user: User,
    provider: CaptureProvider,
    *,
    credential_secret: str | None = None,
    access_token: str | None = None,
) -> ProviderConnection:
    assert user.id is not None
    row = ProviderConnection(
        user_id=user.id,
        provider=provider,
        credential_secret=credential_secret,
        access_token=access_token,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class TestListConnections:
    def test_lists_every_provider_with_only_the_callers_connection_state(
        self, client: TestClient, db_session: Session
    ) -> None:
        owner_headers, owner = _headers(db_session, "provider-list-owner")
        _other_headers, other = _headers(db_session, "provider-list-other")
        _connection(
            db_session,
            owner,
            CaptureProvider.CULTS,
            credential_secret="private-user\nprivate-password",
        )
        _connection(
            db_session,
            other,
            CaptureProvider.MYMINIFACTORY,
            access_token="foreign-access-token",
        )

        response = client.get("/api/v1/provider-connections", headers=owner_headers)

        assert response.status_code == 200, response.text
        by_provider = {item["provider"]: item for item in response.json()}
        assert by_provider == {
            "myminifactory": {
                "provider": "myminifactory",
                "connected": False,
                "updated_at": None,
            },
            "cults": {
                "provider": "cults",
                "connected": True,
                "updated_at": by_provider["cults"]["updated_at"],
            },
        }

    def test_does_not_expose_another_users_connection_state(
        self, client: TestClient, db_session: Session
    ) -> None:
        owner_headers, owner = _headers(db_session, "provider-state-owner")
        other_headers, _other = _headers(db_session, "provider-state-other")
        _connection(
            db_session,
            owner,
            CaptureProvider.CULTS,
            credential_secret="private-user\nprivate-password",
        )

        owner_response = client.get(
            "/api/v1/provider-connections", headers=owner_headers
        )
        other_response = client.get(
            "/api/v1/provider-connections", headers=other_headers
        )

        assert owner_response.status_code == 200, owner_response.text
        assert other_response.status_code == 200, other_response.text
        assert {row["provider"]: row["connected"] for row in owner_response.json()}[
            "cults"
        ] is True
        assert {row["provider"]: row["connected"] for row in other_response.json()}[
            "cults"
        ] is False

    def test_never_returns_stored_provider_secrets(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, owner = _headers(db_session, "provider-list-secret")
        _connection(
            db_session,
            owner,
            CaptureProvider.CULTS,
            credential_secret="private-user\nprivate-password",
            access_token="private-access-token",
        )

        response = client.get("/api/v1/provider-connections", headers=headers)

        assert response.status_code == 200, response.text
        assert "private-user" not in response.text
        assert "private-password" not in response.text
        assert "private-access-token" not in response.text


class TestConnectCults:
    def test_connects_verified_credentials_without_returning_the_secret(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, owner = _headers(db_session, "cults-connect-owner")

        async def accept_credentials(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(
            provider_service.CultsMetadataClient,
            "validate_credentials",
            accept_credentials,
        )

        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json={"username": "private-user", "password": "private-password"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["connected"] is True
        assert "private-user" not in response.text
        assert "private-password" not in response.text
        row = db_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == owner.id,
                ProviderConnection.provider == CaptureProvider.CULTS,
            )
        ).one()
        assert row.credential_secret == "private-user\nprivate-password"
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

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"password": "password"}, id="missing-username"),
            pytest.param({"username": "user"}, id="missing-password"),
            pytest.param({"username": "", "password": "password"}, id="empty-username"),
            pytest.param({"username": "user", "password": ""}, id="empty-password"),
            pytest.param(
                {"username": "user", "password": "password", "token": "extra"},
                id="extra-field",
            ),
        ],
    )
    def test_rejects_invalid_credentials_before_provider_egress(
        self, client: TestClient, db_session: Session, body: dict[str, str]
    ) -> None:
        headers, _owner = _headers(db_session, f"invalid-cults-{len(body)}")

        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json=body,
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ProviderConnection)).all() == []

    def test_does_not_persist_credentials_when_provider_validation_fails(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, _owner = _headers(db_session, "cults-connect-rejected")

        async def reject_credentials(*_args: object, **_kwargs: object) -> None:
            raise provider_service.ProviderConnectionError("provider_auth_failed")

        monkeypatch.setattr(
            provider_service.CultsMetadataClient,
            "validate_credentials",
            reject_credentials,
        )

        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json={"username": "private-user", "password": "private-password"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "provider_connection_validation_failed"
        assert db_session.exec(select(ProviderConnection)).all() == []
        assert "private-password" not in response.text

    def test_replaces_the_callers_existing_credentials(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, owner = _headers(db_session, "cults-reconnect-owner")

        async def accept_credentials(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(
            provider_service.CultsMetadataClient,
            "validate_credentials",
            accept_credentials,
        )
        first = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json={"username": "first-user", "password": "first-password"},
        )
        assert first.status_code == 200, first.text

        response = client.post(
            "/api/v1/provider-connections/cults/connect",
            headers=headers,
            json={"username": "second-user", "password": "second-password"},
        )

        assert response.status_code == 200, response.text
        rows = db_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == owner.id,
                ProviderConnection.provider == CaptureProvider.CULTS,
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].credential_secret == "second-user\nsecond-password"


class TestAuthorizeMyMiniFactory:
    def test_returns_a_caller_bound_authorization_url(
        self, client: TestClient, db_session: Session
    ) -> None:
        _overlay["mmf_client_id"] = "client-id"
        _overlay["mmf_client_secret"] = "client-secret"
        headers, owner = _headers(db_session, "mmf-authorize-owner")

        response = client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=headers,
        )

        assert response.status_code == 200, response.text
        authorization_url = response.json()["authorization_url"]
        assert "client_id=client-id" in authorization_url
        assert "client-secret" not in authorization_url
        state = db_session.exec(select(ProviderOAuthState)).one()
        assert state.user_id == owner.id

    def test_refuses_authorization_when_provider_oauth_is_unconfigured(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "mmf-authorize-unconfigured")
        states_before = db_session.exec(select(ProviderOAuthState)).all()

        response = client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=headers,
        )

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "provider_not_configured"
        assert db_session.exec(select(ProviderOAuthState)).all() == states_before


class TestMyMiniFactoryCallback:
    @staticmethod
    def _authorization_state(
        client: TestClient, db_session: Session, username: str
    ) -> tuple[str, User]:
        _overlay["mmf_client_id"] = "client-id"
        _overlay["mmf_client_secret"] = "client-secret"
        headers, owner = _headers(db_session, username)
        response = client.post(
            "/api/v1/provider-connections/myminifactory/authorize", headers=headers
        )
        assert response.status_code == 200, response.text
        state = response.json()["authorization_url"].split("state=")[1].split("&")[0]
        return state, owner

    def test_callback_connects_the_initiating_user(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ExchangeClient:
            async def exchange_code(self, *_args: object, **_kwargs: object):
                return provider_service.MyMiniFactoryTokens("access", "refresh", 3600)

        monkeypatch.setattr(
            provider_service, "get_mmf_client", lambda: ExchangeClient()
        )
        state, owner = self._authorization_state(
            client, db_session, "mmf-callback-owner"
        )

        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback",
            params={"state": state, "code": "valid-code"},
        )

        assert response.status_code == 200, response.text
        connection = db_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY
            )
        ).one()
        assert connection.user_id == owner.id
        assert connection.access_token == "access"

    def test_callback_rejects_a_replayed_state(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ExchangeClient:
            async def exchange_code(self, *_args: object, **_kwargs: object):
                return provider_service.MyMiniFactoryTokens("access", "refresh", 3600)

        monkeypatch.setattr(
            provider_service, "get_mmf_client", lambda: ExchangeClient()
        )
        state, _owner = self._authorization_state(
            client, db_session, "mmf-replay-owner"
        )
        first = client.get(
            "/api/v1/provider-connections/myminifactory/callback",
            params={"state": state, "code": "first-code"},
        )
        assert first.status_code == 200, first.text

        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback",
            params={"state": state, "code": "replayed-code"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"

    def test_callback_exchange_failure_does_not_create_a_connection(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class ExchangeClient:
            async def exchange_code(self, *_args: object, **_kwargs: object):
                raise provider_service.ProviderConnectionError("provider_auth_failed")

        monkeypatch.setattr(
            provider_service, "get_mmf_client", lambda: ExchangeClient()
        )
        state, _owner = self._authorization_state(
            client, db_session, "mmf-exchange-failure-owner"
        )

        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback",
            params={"state": state, "code": "rejected-code"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"
        assert db_session.exec(select(ProviderConnection)).all() == []

    @pytest.mark.parametrize(
        "query",
        [
            pytest.param({}, id="missing-both"),
            pytest.param({"state": "state"}, id="missing-code"),
            pytest.param({"code": "code"}, id="missing-state"),
        ],
    )
    def test_rejects_callback_with_missing_state_or_code(
        self, client: TestClient, query: dict[str, str]
    ) -> None:
        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback", params=query
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"


class TestDisconnect:
    def test_disconnects_only_the_callers_selected_provider(
        self, client: TestClient, db_session: Session
    ) -> None:
        owner_headers, owner = _headers(db_session, "disconnect-owner")
        _other_headers, other = _headers(db_session, "disconnect-other")
        _connection(db_session, owner, CaptureProvider.CULTS, credential_secret="a\nb")
        foreign = _connection(
            db_session, other, CaptureProvider.CULTS, credential_secret="c\nd"
        )

        response = client.delete(
            "/api/v1/provider-connections/cults/disconnect", headers=owner_headers
        )

        assert response.status_code == 204, response.text
        remaining = db_session.exec(select(ProviderConnection)).all()
        assert [row.id for row in remaining] == [foreign.id]

    def test_disconnect_is_idempotent_when_the_connection_is_absent(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "disconnect-absent")

        response = client.delete(
            "/api/v1/provider-connections/cults/disconnect", headers=headers
        )

        assert response.status_code == 204, response.text
        assert db_session.exec(select(ProviderConnection)).all() == []

    def test_rejects_an_unsupported_provider(
        self, client: TestClient, db_session: Session
    ) -> None:
        headers, _owner = _headers(db_session, "disconnect-unsupported")

        response = client.delete(
            "/api/v1/provider-connections/unsupported/disconnect",
            headers=headers,
        )

        assert response.status_code == 422, response.text


class TestRouterAuthentication:
    @pytest.mark.parametrize(
        ("method", "path", "request_kwargs"),
        [
            pytest.param("GET", "/api/v1/provider-connections", {}, id="list"),
            pytest.param(
                "POST",
                "/api/v1/provider-connections/cults/connect",
                {"json": {"username": "user", "password": "password"}},
                id="connect-cults",
            ),
            pytest.param(
                "POST",
                "/api/v1/provider-connections/myminifactory/authorize",
                {},
                id="authorize-mmf",
            ),
            pytest.param(
                "DELETE",
                "/api/v1/provider-connections/cults/disconnect",
                {},
                id="disconnect",
            ),
        ],
    )
    def test_requires_authentication_outside_the_public_callback(
        self,
        client: TestClient,
        method: str,
        path: str,
        request_kwargs: dict[str, object],
    ) -> None:
        response = client.request(method, path, **request_kwargs)

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(
        ("method", "path", "request_kwargs"),
        [
            pytest.param(
                "POST",
                "/api/v1/provider-connections/cults/connect",
                {"json": {"username": "user", "password": "password"}},
                id="connect-cults",
            ),
            pytest.param(
                "POST",
                "/api/v1/provider-connections/myminifactory/authorize",
                {},
                id="authorize-mmf",
            ),
            pytest.param(
                "DELETE",
                "/api/v1/provider-connections/cults/disconnect",
                {},
                id="disconnect",
            ),
        ],
    )
    def test_rejects_read_scope_for_connection_mutations(
        self,
        client: TestClient,
        db_session: Session,
        method: str,
        path: str,
        request_kwargs: dict[str, object],
    ) -> None:
        headers, _owner = _headers(
            db_session, f"read-scope-{method}-{len(path)}", scope="read"
        )

        response = client.request(method, path, headers=headers, **request_kwargs)

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "insufficient_scope"

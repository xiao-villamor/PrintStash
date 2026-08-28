"""The MyMiniFactory OAuth handshake: authorize, then callback.

Two properties make this safe rather than merely working. The state is **one-time**: it
is reserved by a conditional update *before* the code is exchanged, so a replayed or
concurrent callback finds it spent and never trades the same code twice. And a callback
that fails still commits that reservation — a callback URL sits in a browser history and
gets re-opened, and a state that survives a failure is a state an attacker can retry.

The authorize endpoint is also honest about a deployment that has no MyMiniFactory
credentials: 503 with the reason, and no state row written for a handshake that cannot
finish.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import CaptureProvider, ProviderConnection, ProviderOAuthState
from app.services import provider_connections as service
from app.services.capture_provider_connections import (
    MyMiniFactoryTokens,
    ProviderConnectionError,
)
from tests.integration.api.v1.provider_connections.conftest import MMF_CLIENT_ID

TOKENS = MyMiniFactoryTokens("access-token", "refresh-token", 3600)


class _ExchangeClient:
    def __init__(self, tokens: MyMiniFactoryTokens | None = None) -> None:
        self.tokens = tokens
        self.codes: list[str] = []

    async def exchange_code(self, _credentials, *, code: str, redirect_uri: str):
        self.codes.append(code)
        if self.tokens is None:
            raise ProviderConnectionError("provider_auth_failed")
        return self.tokens


@pytest.fixture
def exchange_succeeds(monkeypatch: pytest.MonkeyPatch) -> _ExchangeClient:
    """Stand in for the token endpoint — the only mocked seam in this file."""
    client = _ExchangeClient(TOKENS)
    monkeypatch.setattr(service, "get_mmf_client", lambda: client)
    return client


@pytest.fixture
def exchange_fails(monkeypatch: pytest.MonkeyPatch) -> _ExchangeClient:
    client = _ExchangeClient(None)
    monkeypatch.setattr(service, "get_mmf_client", lambda: client)
    return client


def _state_from(authorization_url: str) -> str:
    return parse_qs(urlparse(authorization_url).query)["state"][0]


class TestAuthorizeMyMiniFactory:
    def test_hands_back_the_providers_authorization_url(
        self, client: TestClient, user_headers, mmf_configured
    ) -> None:
        response = client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=user_headers("mmf-authorize"),
        )

        assert response.status_code == 200, response.text
        query = parse_qs(urlparse(response.json()["authorization_url"]).query)
        assert query["client_id"] == [MMF_CLIENT_ID]
        assert query["response_type"] == ["code"]

    def test_points_the_provider_back_at_this_deployments_callback(
        self, client: TestClient, user_headers, mmf_configured
    ) -> None:
        response = client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=user_headers("mmf-redirect-uri"),
        )

        query = parse_qs(urlparse(response.json()["authorization_url"]).query)
        assert query["redirect_uri"] == [
            "http://testserver/api/v1/provider-connections/myminifactory/callback"
        ]

    def test_reserves_the_state_for_the_caller(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        mmf_configured,
    ) -> None:
        user = make_user("mmf-state-owner")

        client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=headers_for(user),
        )

        row = db_session.exec(select(ProviderOAuthState)).one()
        assert row.user_id == user.id
        assert row.used_at is None

    def test_refuses_when_the_deployment_has_no_provider_credentials(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=user_headers("mmf-unconfigured"),
        )

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "provider_not_configured"

    def test_writes_no_state_for_a_handshake_that_cannot_finish(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        client.post(
            "/api/v1/provider-connections/myminifactory/authorize",
            headers=user_headers("mmf-unconfigured-empty"),
        )

        assert db_session.exec(select(ProviderOAuthState)).all() == []

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, mmf_configured
    ) -> None:
        response = client.post("/api/v1/provider-connections/myminifactory/authorize")

        assert response.status_code == 401, response.text


class TestMyMiniFactoryCallback:
    def test_connects_the_account_that_started_the_handshake(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        mmf_configured,
        exchange_succeeds,
    ) -> None:
        user = make_user("mmf-callback-owner")
        state = _state_from(
            client.post(
                "/api/v1/provider-connections/myminifactory/authorize",
                headers=headers_for(user),
            ).json()["authorization_url"]
        )
        db_session.close()

        response = client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "connected"}

    def test_stores_the_tokens_it_exchanged_the_code_for(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        mmf_configured,
        exchange_succeeds,
    ) -> None:
        user = make_user("mmf-callback-tokens")
        state = _state_from(
            client.post(
                "/api/v1/provider-connections/myminifactory/authorize",
                headers=headers_for(user),
            ).json()["authorization_url"]
        )
        db_session.close()

        client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=auth-code"
        )

        row = db_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY
            )
        ).one()
        assert row.user_id == user.id
        assert row.access_token == TOKENS.access_token
        assert row.refresh_token == TOKENS.refresh_token
        assert exchange_succeeds.codes == ["auth-code"]

    def test_refuses_a_callback_with_no_state(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback?code=x"
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"

    def test_refuses_a_callback_with_no_code(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback?state=whatever"
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"

    def test_refuses_a_state_this_deployment_never_issued(
        self, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/provider-connections/myminifactory/callback?state=forged&code=x"
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"

    def test_refuses_a_state_that_was_already_spent(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        mmf_configured,
        exchange_succeeds,
    ) -> None:
        state = _state_from(
            client.post(
                "/api/v1/provider-connections/myminifactory/authorize",
                headers=user_headers("mmf-replay"),
            ).json()["authorization_url"]
        )
        db_session.close()
        url = (
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        )
        assert client.get(url).status_code == 200

        replay = client.get(url)

        assert replay.status_code == 400, replay.text
        assert replay.json()["detail"] == "invalid_oauth_callback"

    def test_refuses_a_state_that_has_expired(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        mmf_configured,
        exchange_succeeds,
    ) -> None:
        state = _state_from(
            client.post(
                "/api/v1/provider-connections/myminifactory/authorize",
                headers=user_headers("mmf-expired"),
            ).json()["authorization_url"]
        )
        row = db_session.exec(select(ProviderOAuthState)).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()
        db_session.close()

        response = client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"

    def test_refuses_when_the_provider_rejects_the_code(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        mmf_configured,
        exchange_fails,
    ) -> None:
        state = _state_from(
            client.post(
                "/api/v1/provider-connections/myminifactory/authorize",
                headers=user_headers("mmf-bad-code"),
            ).json()["authorization_url"]
        )
        db_session.close()

        response = client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_oauth_callback"

    def test_spends_the_state_even_when_the_provider_rejects_the_code(
        self,
        client: TestClient,
        db_session: Session,
        user_headers,
        mmf_configured,
        exchange_fails,
    ) -> None:
        state = _state_from(
            client.post(
                "/api/v1/provider-connections/myminifactory/authorize",
                headers=user_headers("mmf-bad-code-spent"),
            ).json()["authorization_url"]
        )
        db_session.close()

        client.get(
            f"/api/v1/provider-connections/myminifactory/callback?state={state}&code=x"
        )

        # A callback URL lives in a browser history; a state that survives a failure
        # is a state that can be retried.
        assert db_session.exec(select(ProviderOAuthState)).one().used_at is not None

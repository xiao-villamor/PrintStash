"""OIDC router state, callback, and public discovery remain safe at the boundary."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import User
from app.services import oidc


def _enable_oidc() -> None:
    _overlay.update(
        {
            "oidc_enabled": True,
            "oidc_issuer_url": "https://id.example.test/application/o/printstash",
            "oidc_client_id": "printstash",
            "oidc_client_secret": "fake-client-secret",
            "oidc_admin_groups": "vault-admins,operators",
            "oidc_display_name": "Authentik",
        }
    )


def _install_login(monkeypatch: pytest.MonkeyPatch, *, state: str = "state") -> None:
    async def begin_login(_redirect_uri: str) -> oidc.OIDCLogin:
        return oidc.OIDCLogin(
            authorization_url=(
                "https://id.example.test/authorize"
                f"?state={state}&code_challenge=challenge&nonce=nonce"
            ),
            state=state,
            nonce="nonce",
            code_verifier="verifier",
        )

    monkeypatch.setattr(oidc, "begin_login", begin_login)


def _start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, state: str = "state"
):
    _enable_oidc()
    _install_login(monkeypatch, state=state)
    response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 302, response.text
    return response


class TestAuthProviders:
    def test_reports_enabled_authentication_providers_publicly(
        self, client: TestClient
    ) -> None:
        _enable_oidc()

        response = client.get("/api/v1/auth/providers")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "oidc_enabled": True,
            "oidc_display_name": "Authentik",
        }

    def test_reports_local_only_authentication_publicly(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/auth/providers")

        assert response.status_code == 200, response.text
        assert response.json()["oidc_enabled"] is False

    def test_omits_oidc_secrets_from_provider_discovery(
        self, client: TestClient
    ) -> None:
        _enable_oidc()

        response = client.get("/api/v1/auth/providers")

        assert response.status_code == 200, response.text
        assert "fake-client-secret" not in response.text
        assert "client_secret" not in response.text


class TestOidcLogin:
    def test_starts_oidc_login_with_state_and_pkce(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = _start(client, monkeypatch, state="expected-state")

        assert "state=expected-state" in response.headers["location"]
        assert "code_challenge=challenge" in response.headers["location"]
        cookies = response.headers.get_list("set-cookie")
        assert any("printstash_oidc_state=expected-state" in value for value in cookies)
        assert any("printstash_oidc_nonce=nonce" in value for value in cookies)
        assert any("printstash_oidc_verifier=verifier" in value for value in cookies)
        assert all("HttpOnly" in value for value in cookies)

    def test_rejects_oidc_login_when_disabled(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def reject(_redirect_uri: str):
            raise oidc.OIDCError("oidc_not_configured")

        monkeypatch.setattr(oidc, "begin_login", reject)

        response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)

        assert response.status_code == 503, response.text
        assert response.json() == {"detail": "oidc_not_configured"}

    def test_surfaces_oidc_discovery_failure_safely(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_oidc()

        async def reject(_redirect_uri: str):
            raise oidc.OIDCError("oidc_discovery_failed")

        monkeypatch.setattr(oidc, "begin_login", reject)

        response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)

        assert response.status_code == 503, response.text
        assert response.json() == {"detail": "oidc_discovery_failed"}
        assert "fake-client-secret" not in response.text


class TestOidcCallback:
    def test_completes_oidc_callback_for_a_valid_response(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _start(client, monkeypatch)

        async def exchange(*_args) -> dict:
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "valid-callback-subject",
                "preferred_username": "valid-callback-user",
            }

        monkeypatch.setattr(oidc, "exchange_code", exchange)

        response = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=state",
            follow_redirects=False,
        )

        assert response.status_code == 302, response.text
        assert response.headers["location"] == "/login?oidc=success"
        assert "printstash_session=" in response.headers["set-cookie"]

    def test_provisions_a_first_time_oidc_user(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _start(client, monkeypatch)

        async def exchange(*_args) -> dict:
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "new-oidc-subject",
                "preferred_username": "new-oidc-user",
                "email": "new@example.test",
            }

        monkeypatch.setattr(oidc, "exchange_code", exchange)

        response = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=state",
            follow_redirects=False,
        )

        assert response.headers["location"] == "/login?oidc=success"
        users = db_session.exec(
            select(User).where(User.oidc_subject == "new-oidc-subject")
        ).all()
        assert len(users) == 1
        assert users[0].oidc_managed is True

    def test_maps_a_configured_oidc_admin_group(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _start(client, monkeypatch)

        async def exchange(*_args) -> dict:
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "admin-oidc-subject",
                "preferred_username": "oidc-admin",
                "groups": ["vault-admins"],
            }

        monkeypatch.setattr(oidc, "exchange_code", exchange)

        client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=state",
            follow_redirects=False,
        )

        user = db_session.exec(
            select(User).where(User.oidc_subject == "admin-oidc-subject")
        ).one()
        assert user.is_superuser is True

    def test_does_not_map_an_unconfigured_oidc_group(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _start(client, monkeypatch)

        async def exchange(*_args) -> dict:
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "regular-oidc-subject",
                "preferred_username": "oidc-regular",
                "groups": ["unrelated-group"],
            }

        monkeypatch.setattr(oidc, "exchange_code", exchange)

        client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=state",
            follow_redirects=False,
        )

        user = db_session.exec(
            select(User).where(User.oidc_subject == "regular-oidc-subject")
        ).one()
        assert user.is_superuser is False

    def test_rejects_a_missing_oidc_callback_code(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _start(client, monkeypatch)

        response = client.get(
            "/api/v1/auth/oidc/callback?state=state", follow_redirects=False
        )

        assert response.headers["location"] == "/login?oidc_error=invalid_state"

    def test_rejects_a_missing_oidc_callback_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _start(client, monkeypatch)

        response = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code",
            follow_redirects=False,
        )

        assert response.headers["location"] == "/login?oidc_error=invalid_state"

    def test_rejects_a_tampered_or_expired_oidc_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _start(client, monkeypatch, state="expected-state")

        response = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=tampered",
            follow_redirects=False,
        )

        assert response.headers["location"] == "/login?oidc_error=invalid_state"

    def test_rejects_replay_of_an_oidc_state(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _start(client, monkeypatch)

        async def exchange(*_args) -> dict:
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "replay-subject",
                "preferred_username": "replay-user",
            }

        monkeypatch.setattr(oidc, "exchange_code", exchange)
        first = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=state",
            follow_redirects=False,
        )
        assert first.headers["location"] == "/login?oidc=success"

        response = client.get(
            "/api/v1/auth/oidc/callback?code=replay-code&state=state",
            follow_redirects=False,
        )

        assert response.headers["location"] == "/login?oidc_error=invalid_state"

    def test_reports_an_oidc_provider_denial_safely(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _start(client, monkeypatch)

        response = client.get(
            "/api/v1/auth/oidc/callback?error=access_denied",
            follow_redirects=False,
        )

        assert response.headers["location"] == "/login?oidc_error=provider_rejected"
        assert "access_denied" not in response.headers["location"]

    @pytest.mark.parametrize(
        "error_code",
        [
            pytest.param("oidc_issuer_mismatch", id="issuer"),
            pytest.param("oidc_invalid_audience", id="audience"),
            pytest.param("oidc_nonce_mismatch", id="nonce"),
            pytest.param("oidc_invalid_id_token", id="signature"),
        ],
    )
    def test_rejects_invalid_oidc_identity_proofs(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        error_code: str,
    ) -> None:
        _start(client, monkeypatch)

        async def exchange(*_args):
            raise oidc.OIDCError(error_code)

        monkeypatch.setattr(oidc, "exchange_code", exchange)

        response = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=state",
            follow_redirects=False,
        )

        assert response.headers["location"] == f"/login?oidc_error={error_code}"

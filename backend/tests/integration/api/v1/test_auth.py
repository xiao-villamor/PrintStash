"""Signing in, staying in, and getting out — the credential surface.

Four properties decide whether a self-hosted vault is safe to expose, and each one is a
group below. A **refresh token is single-use**: presenting it returns a new one and
burns the old, so a stolen copy is worth one race at most. A **logout survives a
restart**: the process-local deny list disappears, so revocation has to live in
persisted state or a stolen access token comes back to life with the container. An
**API key is not a bearer token** — it buys a session through `/auth/login` and is
refused in an `Authorization` header, so a key leaked into a log cannot be replayed
directly. And **failed logins are rate-limited**, per limiter, so exhausting one does
not lock out the other.

OIDC's own protocol work lives in `integration/services/test_oidc.py` and
`e2e/test_oidc.py`; what is here is the router's handling of a callback that never gets
that far.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import ApiKey, User
from app.services import oidc
from app.services.auth import ACCESS_BLOCKLIST, create_api_key
from tests.factories import build_user

PASSWORD = "Password123"


@pytest.fixture
def account(db_session: Session):
    """Create a user and return them with a freshly logged-in session."""

    def build(username: str, *, is_superuser: bool = False) -> User:
        user = build_user(
            db_session,
            username=username,
            password=PASSWORD,
            superuser=is_superuser,
            active=True,
        )
        return user

    return build


@pytest.fixture
def sign_in(client: TestClient):
    def login(username: str, **body: Any) -> dict:
        payload = {"username": username, "password": PASSWORD}
        payload.update(body)
        response = client.post("/api/v1/auth/login", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    return login


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAuthProviders:
    def test_reports_oidc_as_disabled_by_default(self, client: TestClient) -> None:
        body = client.get("/api/v1/auth/providers").json()

        assert body["oidc_enabled"] is False

    def test_needs_no_authentication(self, client: TestClient) -> None:
        # The login page reads this before anyone has signed in.
        assert client.get("/api/v1/auth/providers").status_code == 200


class TestLogin:
    def test_returns_an_access_token(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("alice")

        body = sign_in("alice")

        assert body["token_type"] == "bearer"
        assert body["access_token"]

    def test_returns_a_refresh_token(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("alice")

        assert sign_in("alice")["refresh_token"]

    def test_scopes_a_plain_user_to_write(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("alice")

        assert sign_in("alice")["scope"] == "write"

    def test_scopes_a_superuser_to_admin(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("root", is_superuser=True)

        assert sign_in("root")["scope"] == "admin"

    def test_sets_an_httponly_session_cookie(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("cookie-user")

        client.post(
            "/api/v1/auth/login",
            json={"username": "cookie-user", "password": PASSWORD},
        )

        cookie = client.cookies.get("printstash_session")
        assert cookie, "the browser session rides in a cookie, not in JS-readable state"

    def test_authenticates_the_browser_by_cookie_alone(
        self, client: TestClient, account
    ) -> None:
        account("cookie-user")
        client.post(
            "/api/v1/auth/login",
            json={"username": "cookie-user", "password": PASSWORD},
        )

        assert client.get("/api/v1/auth/me").status_code == 200

    def test_accepts_an_api_key_instead_of_a_password(
        self, client: TestClient, db_session: Session, account
    ) -> None:
        user = account("script-user")
        _, raw_key = create_api_key(db_session, user.id, "Orca uploader")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "script-user", "api_key": raw_key},
        )

        assert response.status_code == 200, response.text
        assert response.json()["access_token"]

    def test_rejects_a_request_carrying_two_credentials(
        self, client: TestClient, db_session: Session, account
    ) -> None:
        user = account("dual-user")
        _, raw_key = create_api_key(db_session, user.id, "Bad client")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "dual-user", "password": PASSWORD, "api_key": raw_key},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "provide_password_or_api_key"

    def test_rejects_a_wrong_password(self, client: TestClient, account) -> None:
        account("alice")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "not-the-password"},
        )

        assert response.status_code == 401, response.text

    def test_rejects_an_unknown_user(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": PASSWORD},
        )

        assert response.status_code == 401, response.text


class TestApiKeys:
    def test_returns_the_new_key_once(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("key-owner")
        headers = _bearer(sign_in("key-owner")["access_token"])

        created = client.post(
            "/api/v1/auth/api-keys", json={"name": "Uploader"}, headers=headers
        )

        assert created.status_code == 200, created.text
        assert created.json()["name"] == "Uploader"
        assert created.json()["api_key"].startswith("psk_")

    def test_lists_the_callers_keys(self, client: TestClient, account, sign_in) -> None:
        account("key-owner")
        headers = _bearer(sign_in("key-owner")["access_token"])
        created = client.post(
            "/api/v1/auth/api-keys", json={"name": "Uploader"}, headers=headers
        ).json()

        listed = client.get("/api/v1/auth/api-keys", headers=headers).json()

        assert [row["id"] for row in listed] == [created["id"]]

    def test_never_lists_the_secret(self, client: TestClient, account, sign_in) -> None:
        account("key-owner")
        headers = _bearer(sign_in("key-owner")["access_token"])
        created = client.post(
            "/api/v1/auth/api-keys", json={"name": "Uploader"}, headers=headers
        ).json()

        listed = client.get("/api/v1/auth/api-keys", headers=headers).text

        assert created["api_key"] not in listed

    def test_revokes_a_key(self, client: TestClient, account, sign_in) -> None:
        account("key-owner")
        headers = _bearer(sign_in("key-owner")["access_token"])
        created = client.post(
            "/api/v1/auth/api-keys", json={"name": "Uploader"}, headers=headers
        ).json()

        deleted = client.delete(
            f"/api/v1/auth/api-keys/{created['id']}", headers=headers
        )

        assert deleted.status_code == 204, deleted.text

    def test_a_revoked_key_no_longer_signs_in(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("key-owner")
        headers = _bearer(sign_in("key-owner")["access_token"])
        created = client.post(
            "/api/v1/auth/api-keys", json={"name": "Uploader"}, headers=headers
        ).json()
        client.delete(f"/api/v1/auth/api-keys/{created['id']}", headers=headers)

        relogin = client.post(
            "/api/v1/auth/login",
            json={"username": "key-owner", "api_key": created["api_key"]},
        )

        assert relogin.status_code == 401, relogin.text

    def test_refuses_to_revoke_someone_elses_key(
        self, client: TestClient, db_session: Session, account, sign_in
    ) -> None:
        owner = account("key-owner")
        stranger = account("key-stranger")
        key, _ = create_api_key(db_session, owner.id, "Not yours")
        headers = _bearer(sign_in("key-stranger")["access_token"])

        response = client.delete(f"/api/v1/auth/api-keys/{key.id}", headers=headers)

        assert response.status_code == 404, "another user's key is not even visible"
        assert db_session.get(ApiKey, key.id) is not None
        assert stranger.id != owner.id

    def test_reports_an_unknown_key_as_not_found(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("key-owner")
        headers = _bearer(sign_in("key-owner")["access_token"])

        response = client.delete("/api/v1/auth/api-keys/9999", headers=headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "api_key_not_found"

    def test_an_api_key_is_not_a_bearer_token(
        self, client: TestClient, db_session: Session, account
    ) -> None:
        user = account("direct-key")
        _, raw_key = create_api_key(db_session, user.id, "Direct header")

        response = client.get("/api/v1/auth/me", headers=_bearer(raw_key))

        # A key leaked into a log must not be replayable as a session.
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "not_authenticated"


class TestRefresh:
    def test_issues_a_new_refresh_token(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("bob")
        login = sign_in("bob")

        refreshed = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["refresh_token"] != login["refresh_token"]

    def test_burns_the_presented_token(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("bob")
        login = sign_in("bob")
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        replay = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        assert replay.status_code == 401, "a stolen refresh token is worth one race"
        assert replay.json()["detail"] == "invalid_refresh_token"

    def test_rejects_a_token_that_was_never_real(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "not-a-token"}
        )

        assert response.status_code == 401, response.text

    def test_rejects_a_disabled_users_token(
        self, client: TestClient, db_session: Session, account, sign_in
    ) -> None:
        user = account("suspended")
        login = sign_in("suspended")
        user.is_active = False
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        assert response.status_code == 401, response.text

    def test_burns_a_disabled_users_token_anyway(
        self, client: TestClient, db_session: Session, account, sign_in
    ) -> None:
        user = account("suspended")
        login = sign_in("suspended")
        user.is_active = False
        db_session.add(user)
        db_session.commit()
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        user.is_active = True
        db_session.add(user)
        db_session.commit()

        replay = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        assert replay.status_code == 401, (
            "reactivating an account must not resurrect its old credentials"
        )


class TestLogout:
    def test_revokes_the_access_token(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("carol")
        login = sign_in("carol")

        client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": login["refresh_token"]},
            headers=_bearer(login["access_token"]),
        )

        me = client.get("/api/v1/auth/me", headers=_bearer(login["access_token"]))
        assert me.status_code == 401
        assert me.json()["detail"] == "not_authenticated"

    def test_revokes_the_refresh_token(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("carol")
        login = sign_in("carol")

        client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": login["refresh_token"]},
            headers=_bearer(login["access_token"]),
        )

        refreshed = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        assert refreshed.status_code == 401
        assert refreshed.json()["detail"] == "invalid_refresh_token"

    def test_clears_the_session_cookie(self, client: TestClient, account) -> None:
        account("cookie-user")
        client.post(
            "/api/v1/auth/login",
            json={"username": "cookie-user", "password": PASSWORD},
        )

        client.post("/api/v1/auth/logout")

        assert client.get("/api/v1/auth/me").status_code == 401

    def test_survives_a_restart(self, client: TestClient, account, sign_in) -> None:
        account("durable-logout")
        login = sign_in("durable-logout")
        client.post("/api/v1/auth/logout", headers=_bearer(login["access_token"]))

        # A process-local deny list disappears on restart; revocation must not.
        ACCESS_BLOCKLIST.clear()

        me = client.get("/api/v1/auth/me", headers=_bearer(login["access_token"]))
        assert me.status_code == 401

    def test_ends_every_session_when_no_token_is_named(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("durable-logout")
        first = sign_in("durable-logout")
        second = sign_in("durable-logout")

        client.post("/api/v1/auth/logout", headers=_bearer(first["access_token"]))

        for token in (first["refresh_token"], second["refresh_token"]):
            refreshed = client.post(
                "/api/v1/auth/refresh", json={"refresh_token": token}
            )
            assert refreshed.status_code == 401, "a bare logout ends all sessions"


class TestGetMe:
    def test_returns_the_signed_in_user(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("alice")
        headers = _bearer(sign_in("alice")["access_token"])

        response = client.get("/api/v1/auth/me", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["username"] == "alice"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/auth/me").status_code == 401


class TestOidcRouter:
    def test_reports_a_provider_that_will_not_start_a_login(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def unreachable(_redirect_uri: str):
            raise oidc.OIDCError("provider_unreachable")

        monkeypatch.setattr(oidc, "begin_login", unreachable)

        response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "provider_unreachable"

    def test_sends_a_rejected_callback_back_to_the_login_page(
        self, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/auth/oidc/callback?error=access_denied", follow_redirects=False
        )

        assert response.status_code == 302
        assert "oidc_error=provider_rejected" in response.headers["location"]

    def test_rejects_a_callback_whose_state_does_not_match(
        self, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/auth/oidc/callback?code=abc&state=mismatched",
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert "oidc_error=invalid_state" in response.headers["location"]

    def test_reports_an_expired_callback(self, client: TestClient) -> None:
        client.cookies.set("printstash_oidc_state", "matching-state")

        response = client.get(
            "/api/v1/auth/oidc/callback?code=abc&state=matching-state",
            follow_redirects=False,
        )

        # State matched but the nonce/verifier cookies are gone: the login went
        # stale rather than being tampered with.
        assert response.status_code == 302
        assert "oidc_error=expired" in response.headers["location"]
        client.cookies.clear()


class TestRateLimits:
    def test_blocks_repeated_failed_logins(self, client: TestClient, account) -> None:
        account("bob")

        for _ in range(10):
            attempt = client.post(
                "/api/v1/auth/login",
                json={"username": "bob", "password": "wrong-password"},
            )
            assert attempt.status_code == 401

        blocked = client.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "wrong-password"},
        )
        assert blocked.status_code == 429

    def test_keeps_refresh_working_when_login_is_blocked(
        self, client: TestClient, account, sign_in
    ) -> None:
        account("carol")
        login = sign_in("carol")
        for _ in range(11):
            client.post(
                "/api/v1/auth/login",
                json={"username": "carol", "password": "wrong-password"},
            )

        refreshed = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        assert refreshed.status_code == 200, (
            "one exhausted limiter must not lock out the other"
        )

"""E2E: OpenID Connect login against a real loopback IdP.

Unlike ``tests/test_oidc.py`` (which monkeypatches ``_discovery``/
``_post_token``/``_get_json``), this drives the real HTTP calls in
``app/services/oidc.py`` against a real fake IdP server
(``fakes/mock_oidc_provider.py``) on a real loopback socket: discovery,
JWKS fetch, RS256 signature verification, issuer/audience/nonce checks all
run for real.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.core.config import _overlay
from app.db.models import User
from tests.factories import build_user
from tests.fakes.mock_oidc_provider import FakeOIDCProvider, build_app
from tests.fakes.server import start_server


@pytest.fixture
def idp():
    state = FakeOIDCProvider()
    running = start_server(build_app(state))
    state.issuer = running.base_url
    try:
        yield state
    finally:
        running.stop()


def _enable_oidc(idp: FakeOIDCProvider, *, admin_groups: str = "vault-admins") -> None:
    _overlay.update(
        {
            "oidc_enabled": True,
            "oidc_issuer_url": idp.issuer,
            "oidc_client_id": "printstash-e2e",
            "oidc_allow_insecure_http": True,
            "oidc_admin_groups": admin_groups,
        }
    )


async def _begin_login(api) -> tuple[str, str]:
    """Hit the real login endpoint; return (state, nonce) from the cookie jar."""
    start = await api.get("/api/v1/auth/oidc/login")
    assert start.status_code == 302, start.text
    state = api.cookies.get("printstash_oidc_state")
    nonce = api.cookies.get("printstash_oidc_nonce")
    assert state and nonce
    return state, nonce


class TestOidcLogin:
    @pytest.mark.asyncio
    async def test_a_full_pkce_login_signs_in_a_provisioned_admin(
        self, api, idp, e2e_db
    ):
        _enable_oidc(idp)
        state, nonce = await _begin_login(api)

        code = idp.issue_code(
            {
                "sub": "e2e-user-1",
                "aud": "printstash-e2e",
                "nonce": nonce,
                "preferred_username": "grillmaster",
                "email": "grill@example.test",
                "groups": ["vault-admins"],
            }
        )
        callback = await api.get(
            f"/api/v1/auth/oidc/callback?code={code}&state={state}"
        )
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == "/login?oidc=success"
        assert "printstash_session=" in callback.headers.get("set-cookie", "")

        e2e_db.expire_all()
        user = e2e_db.exec(select(User).where(User.username == "grillmaster")).one()
        assert user.is_superuser is True
        assert user.oidc_issuer == idp.issuer
        assert user.oidc_subject == "e2e-user-1"
        assert user.email == "grill@example.test"

    @pytest.mark.asyncio
    async def test_username_collision_gets_a_unique_suffix(self, api, idp, e2e_db):

        build_user(e2e_db, username="grillmaster", password="Password123", active=True)

        _enable_oidc(idp)
        state, nonce = await _begin_login(api)
        code = idp.issue_code(
            {
                "sub": "e2e-user-3",
                "aud": "printstash-e2e",
                "nonce": nonce,
                "preferred_username": "grillmaster",
            }
        )
        callback = await api.get(
            f"/api/v1/auth/oidc/callback?code={code}&state={state}"
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/login?oidc=success"

        e2e_db.expire_all()
        provisioned = e2e_db.exec(
            select(User).where(User.oidc_subject == "e2e-user-3")
        ).one()
        assert provisioned.username != "grillmaster"
        assert provisioned.username.startswith("grillmaster")

    @pytest.mark.asyncio
    async def test_state_mismatch_is_rejected(self, api, idp, e2e_db):
        _enable_oidc(idp)
        _state, nonce = await _begin_login(api)

        code = idp.issue_code(
            {
                "sub": "e2e-user-4",
                "aud": "printstash-e2e",
                "nonce": nonce,
                "preferred_username": "sneaky",
            }
        )
        callback = await api.get(
            f"/api/v1/auth/oidc/callback?code={code}&state=not-the-real-state"
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/login?oidc_error=invalid_state"
        assert "printstash_session=" not in callback.headers.get("set-cookie", "")

        e2e_db.expire_all()
        assert (
            e2e_db.exec(select(User).where(User.oidc_subject == "e2e-user-4")).first()
            is None
        )


class TestUser:
    @pytest.mark.asyncio
    async def test_non_admin_group_provisions_regular_user(self, api, idp, e2e_db):
        _enable_oidc(idp)
        state, nonce = await _begin_login(api)

        code = idp.issue_code(
            {
                "sub": "e2e-user-2",
                "aud": "printstash-e2e",
                "nonce": nonce,
                "preferred_username": "regularjoe",
                "groups": ["some-other-group"],
            }
        )
        callback = await api.get(
            f"/api/v1/auth/oidc/callback?code={code}&state={state}"
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/login?oidc=success"

        e2e_db.expire_all()
        user = e2e_db.exec(select(User).where(User.username == "regularjoe")).one()
        assert user.is_superuser is False

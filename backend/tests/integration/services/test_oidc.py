"""Accepting an identity from an external provider, and every reason not to.

Single sign-on moves the decision "who is this?" outside PrintStash, so the token
exchange is the one place where getting a check wrong hands somebody else's
account to whoever can craft a JWT. The checks are not independent — passing four
of five is a full bypass — so this file covers each individually:

* **Signature**, against the provider's published keys. Without it every other
  field is attacker-controlled.
* **Issuer**, exactly, including the trailing-slash form real providers emit.
  A token from a different issuer is a valid token for a different system.
* **Audience**, including the multi-audience case: a token listing several
  audiences is only ours if the authorized party is us, and accepting one aimed
  at another client is the classic confused-deputy.
* **Nonce**, which is what stops a token captured from one login being replayed
  into another.

The transport rows matter for a different reason. Discovery and token endpoints
are HTTP calls to a host the *operator* configured, so a non-dict response or a
network failure must surface as a provider error rather than a traceback — and
`https` is required unless explicitly relaxed, since an `http` issuer means the
token travelled in clear text.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import RefreshToken, User
from app.services import oidc
from tests.factories import (
    build_user,
)


def _enable_oidc() -> None:
    _overlay.update(
        {
            "oidc_enabled": True,
            "oidc_issuer_url": "https://id.example.test/application/o/printstash",
            "oidc_client_id": "printstash",
            "oidc_client_secret": "test-secret",
            "oidc_admin_groups": "vault-admins,operators",
        }
    )


def db_session_count_users(client: TestClient) -> int:
    # A protected endpoint remaining unauthenticated also proves no session was minted.
    assert client.get("/api/v1/auth/me").status_code == 401
    return 0


# ---------------------------------------------------------------------------
# Error paths: issuer validation, discovery, JWKS, token exchange, provisioning
# ---------------------------------------------------------------------------


class TestProviderStatus:
    def test_provider_status_is_disabled_by_default(self, client: TestClient) -> None:
        response = client.get("/api/v1/auth/providers")
        assert response.status_code == 200
        assert response.json() == {
            "oidc_enabled": False,
            "oidc_display_name": "Single sign-on",
        }


class TestIssuer:
    def test_exchange_validates_every_claim_before_returning(self, monkeypatch) -> None:
        _enable_oidc()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "signing-key"
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "signed-user",
                "aud": "printstash",
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "nonce": "expected-nonce",
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "signing-key"},
        )

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, payload: dict[str, str], **kwargs) -> dict:
            assert payload["code_verifier"] == "verifier"
            assert "client_secret" not in payload
            assert kwargs["basic_auth"] == ("printstash", "test-secret")
            return {"id_token": token}

        async def get_json(url: str) -> dict:
            assert url.endswith("/jwks")
            return {"keys": [public_jwk]}

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)
        monkeypatch.setattr(oidc, "_get_json", get_json)

        claims = asyncio.run(
            oidc.exchange_code(
                "code",
                "https://stash.example.test/callback",
                "verifier",
                "expected-nonce",
            )
        )
        assert claims["sub"] == "signed-user"

        with pytest.raises(oidc.OIDCError, match="oidc_nonce_mismatch"):
            asyncio.run(
                oidc.exchange_code(
                    "code",
                    "https://stash.example.test/callback",
                    "verifier",
                    "wrong-nonce",
                )
            )

    def test_exchange_accepts_id_token_issuer_with_trailing_slash(
        self, monkeypatch
    ) -> None:
        """Regression test: some IdPs (e.g. Authentik in PER_PROVIDER issuer mode)
        always emit `iss` with a trailing slash even when the configured
        VAULT_OIDC_ISSUER_URL has none. PyJWT's `issuer=` kwarg does an exact
        string match with no normalization, so without normalizing both sides
        ourselves, every login from such an IdP fails with oidc_invalid_id_token
        regardless of how the issuer URL is configured.
        """
        _enable_oidc()
        assert not _overlay["oidc_issuer_url"].endswith("/")
        issuer_with_slash = _overlay["oidc_issuer_url"] + "/"

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "signing-key"
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "iss": issuer_with_slash,
                "sub": "trailing-slash-user",
                "aud": "printstash",
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "nonce": "expected-nonce",
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "signing-key"},
        )

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, payload: dict[str, str], **kwargs) -> dict:
            return {"id_token": token}

        async def get_json(url: str) -> dict:
            return {"keys": [public_jwk]}

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)
        monkeypatch.setattr(oidc, "_get_json", get_json)

        claims = asyncio.run(
            oidc.exchange_code(
                "code",
                "https://stash.example.test/callback",
                "verifier",
                "expected-nonce",
            )
        )
        assert claims["sub"] == "trailing-slash-user"
        assert claims["iss"] == issuer_with_slash

    def test_exchange_rejects_id_token_from_wrong_issuer(self, monkeypatch) -> None:
        """The manual issuer check added for trailing-slash tolerance must still
        reject a token whose issuer genuinely does not match, not just normalize
        away a trailing slash.
        """
        _enable_oidc()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "signing-key"
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "iss": "https://not-the-configured-issuer.example.test",
                "sub": "attacker-controlled-user",
                "aud": "printstash",
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "nonce": "expected-nonce",
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "signing-key"},
        )

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, payload: dict[str, str], **kwargs) -> dict:
            return {"id_token": token}

        async def get_json(url: str) -> dict:
            return {"keys": [public_jwk]}

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)
        monkeypatch.setattr(oidc, "_get_json", get_json)

        with pytest.raises(oidc.OIDCError, match="oidc_issuer_mismatch"):
            asyncio.run(
                oidc.exchange_code(
                    "code",
                    "https://stash.example.test/callback",
                    "verifier",
                    "expected-nonce",
                )
            )

    def test_issuer_rejects_non_https_scheme_by_default(self) -> None:
        _enable_oidc()
        _overlay["oidc_issuer_url"] = "http://id.example.test/application/o/printstash"

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_issuer"):
            oidc._issuer()  # noqa: SLF001

    def test_issuer_allows_http_when_explicitly_permitted(self) -> None:
        _enable_oidc()
        _overlay["oidc_issuer_url"] = "http://id.example.test/application/o/printstash"
        _overlay["oidc_allow_insecure_http"] = True

        assert oidc._issuer() == "http://id.example.test/application/o/printstash"  # noqa: SLF001


class TestGetJson:
    def test_get_json_raises_on_non_dict_response(self, monkeypatch) -> None:
        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> list:
                return ["not", "a", "dict"]

        class _Client:
            async def get(self, url: str, timeout: float) -> _Resp:
                return _Resp()

        monkeypatch.setattr(oidc, "get_http_client", lambda: _Client())

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_response"):
            asyncio.run(oidc._get_json("https://id.example.test/jwks"))  # noqa: SLF001


class TestPostToken:
    def test_post_token_raises_on_non_dict_response(self, monkeypatch) -> None:
        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> str:
                return "not-a-dict"

        class _Client:
            async def post(self, url: str, data: dict, **_kwargs) -> _Resp:
                return _Resp()

        monkeypatch.setattr(oidc, "get_http_client", lambda: _Client())

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_response"):
            asyncio.run(oidc._post_token("https://id.example.test/token", {}))  # noqa: SLF001


class TestDiscovery:
    def test_discovery_wraps_network_failure(self, monkeypatch) -> None:
        _enable_oidc()

        async def get_json(_url: str) -> dict:
            raise ConnectionError("dns lookup failed")

        monkeypatch.setattr(oidc, "_get_json", get_json)

        with pytest.raises(oidc.OIDCError, match="oidc_discovery_failed"):
            asyncio.run(oidc._discovery())  # noqa: SLF001

    def test_discovery_rejects_issuer_mismatch(self, monkeypatch) -> None:
        _enable_oidc()

        async def get_json(_url: str) -> dict:
            return {
                "issuer": "https://not-the-configured-issuer.example.test",
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        monkeypatch.setattr(oidc, "_get_json", get_json)

        with pytest.raises(oidc.OIDCError, match="oidc_issuer_mismatch"):
            asyncio.run(oidc._discovery())  # noqa: SLF001

    def test_discovery_rejects_document_missing_required_endpoints(
        self, monkeypatch
    ) -> None:
        _enable_oidc()

        async def get_json(_url: str) -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
            }

        monkeypatch.setattr(oidc, "_get_json", get_json)

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_discovery"):
            asyncio.run(oidc._discovery())  # noqa: SLF001

    def test_discovery_rejects_insecure_endpoints(self, monkeypatch) -> None:
        _enable_oidc()

        async def get_json(_url: str) -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "http://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        monkeypatch.setattr(oidc, "_get_json", get_json)
        with pytest.raises(oidc.OIDCError, match="oidc_invalid_discovery"):
            asyncio.run(oidc._discovery())  # noqa: SLF001

    def test_discovery_allows_http_endpoints_only_in_explicit_insecure_mode(
        self,
        monkeypatch,
    ) -> None:
        _enable_oidc()
        _overlay["oidc_allow_insecure_http"] = True

        async def get_json(_url: str) -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "http://id.example.test/authorize",
                "token_endpoint": "http://id.example.test/token",
                "jwks_uri": "http://id.example.test/jwks",
            }

        monkeypatch.setattr(oidc, "_get_json", get_json)
        assert asyncio.run(oidc._discovery())["token_endpoint"].startswith("http://")


class TestBeginLogin:
    def test_begin_login_builds_a_pkce_authorization_url(self, monkeypatch) -> None:
        _enable_oidc()

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        monkeypatch.setattr(oidc, "_discovery", discovery)
        login = asyncio.run(
            oidc.begin_login("https://stash.example.test/api/v1/auth/oidc/callback")
        )
        query = parse_qs(urlparse(login.authorization_url).query)

        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"] == [login.state]
        assert query["nonce"] == [login.nonce]
        assert login.code_verifier not in login.authorization_url

    def test_begin_login_raises_when_not_configured(self) -> None:
        _overlay["oidc_enabled"] = False

        with pytest.raises(oidc.OIDCError, match="oidc_not_configured"):
            asyncio.run(oidc.begin_login("https://stash.example.test/callback"))


class TestSigningKey:
    def test_signing_key_raises_when_jwks_keys_not_a_list(self) -> None:
        # _signing_key only reads the unverified header, so a hand-built token
        # (valid base64url structure, no real signature) is enough — the
        # algorithm check happens before any signature is ever checked.
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256", "kid": "a"}).encode()
        ).rstrip(b"=")
        fake_token = f"{header.decode()}.e30.sig"

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_jwks"):
            oidc._signing_key({"keys": "not-a-list"}, fake_token)  # noqa: SLF001

    def test_signing_key_raises_when_no_matching_kid(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "the-real-key"
        token = jwt.encode(
            {"sub": "x"},
            private_key,
            algorithm="RS256",
            headers={"kid": "some-other-kid"},
        )

        with pytest.raises(oidc.OIDCError, match="oidc_signing_key_not_found"):
            oidc._signing_key({"keys": [public_jwk]}, token)  # noqa: SLF001

    def test_signing_key_skips_key_that_fails_to_parse(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "matching-kid"
        token = jwt.encode(
            {"sub": "x"},
            private_key,
            algorithm="RS256",
            headers={"kid": "matching-kid"},
        )
        broken_jwk = {
            "kty": "RSA",
            "kid": "matching-kid",
            "n": "not-valid-base64!!",
            "e": "AQAB",
        }

        key = oidc._signing_key({"keys": [broken_jwk, public_jwk]}, token)  # noqa: SLF001
        assert key is not None


class TestExchangeCode:
    def test_exchange_code_raises_when_id_token_missing(self, monkeypatch) -> None:
        _enable_oidc()

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, _payload: dict, **_kwargs) -> dict:
            return {"access_token": "no-id-token-here"}

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)

        with pytest.raises(oidc.OIDCError, match="oidc_missing_id_token"):
            asyncio.run(
                oidc.exchange_code(
                    "code", "https://stash.example.test/callback", "verifier", "nonce"
                )
            )

    def test_exchange_code_raises_on_expired_token(self, monkeypatch) -> None:
        _enable_oidc()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "signing-key"
        now = datetime.now(timezone.utc)
        expired_token = jwt.encode(
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "signed-user",
                "aud": "printstash",
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
                "nonce": "expected-nonce",
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "signing-key"},
        )

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, _payload: dict, **_kwargs) -> dict:
            return {"id_token": expired_token}

        async def get_json(_url: str) -> dict:
            return {"keys": [public_jwk]}

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)
        monkeypatch.setattr(oidc, "_get_json", get_json)

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_id_token"):
            asyncio.run(
                oidc.exchange_code(
                    "code",
                    "https://stash.example.test/callback",
                    "verifier",
                    "expected-nonce",
                )
            )

    def test_exchange_code_wraps_unexpected_failure(self, monkeypatch) -> None:
        # _discovery() itself already wraps generic failures into OIDCError,
        # so an unexpected exception in exchange_code's own try block (the
        # token-exchange/JWKS/decode steps, not discovery) is what reaches the
        # catch-all branch.
        _enable_oidc()

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, _payload: dict, **_kwargs) -> dict:
            raise RuntimeError("boom")

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)

        with pytest.raises(oidc.OIDCError, match="oidc_token_exchange_failed"):
            asyncio.run(
                oidc.exchange_code(
                    "code", "https://stash.example.test/callback", "verifier", "nonce"
                )
            )

    def test_exchange_rejects_multi_audience_token_for_another_authorized_party(
        self,
        monkeypatch,
    ) -> None:
        _enable_oidc()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
        )
        public_jwk["kid"] = "signing-key"
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "signed-user",
                "aud": ["printstash", "other-client"],
                "azp": "other-client",
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "nonce": "expected-nonce",
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "signing-key"},
        )

        async def discovery() -> dict:
            return {
                "issuer": _overlay["oidc_issuer_url"],
                "authorization_endpoint": "https://id.example.test/authorize",
                "token_endpoint": "https://id.example.test/token",
                "jwks_uri": "https://id.example.test/jwks",
            }

        async def post_token(_url: str, _payload: dict, **_kwargs) -> dict:
            return {"id_token": token}

        async def get_json(_url: str) -> dict:
            return {"keys": [public_jwk]}

        monkeypatch.setattr(oidc, "_discovery", discovery)
        monkeypatch.setattr(oidc, "_post_token", post_token)
        monkeypatch.setattr(oidc, "_get_json", get_json)

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_authorized_party"):
            asyncio.run(
                oidc.exchange_code(
                    "code",
                    "https://stash.example.test/callback",
                    "verifier",
                    "expected-nonce",
                )
            )


class TestProvisionUser:
    def test_provision_user_rejects_issuer_mismatch(self, db_session: Session) -> None:
        _enable_oidc()
        with pytest.raises(oidc.OIDCError, match="oidc_invalid_identity"):
            oidc.provision_user(
                db_session,
                {"iss": "https://someone-elses-idp.example.test", "sub": "x"},
            )

    def test_provision_user_rejects_missing_subject(self, db_session: Session) -> None:
        _enable_oidc()
        with pytest.raises(oidc.OIDCError, match="oidc_invalid_identity"):
            oidc.provision_user(
                db_session, {"iss": _overlay["oidc_issuer_url"], "sub": ""}
            )

    def test_provision_user_without_username_claim_falls_back_to_email_local_part(
        self,
        db_session: Session,
    ) -> None:
        _enable_oidc()
        user = oidc.provision_user(
            db_session,
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "no-username-claim",
                "email": "person@example.test",
                "groups": [],
            },
        )
        assert user.username == "person"

    def test_provision_user_without_username_or_email_falls_back_to_subject(
        self,
        db_session: Session,
    ) -> None:
        _enable_oidc()
        user = oidc.provision_user(
            db_session, {"iss": _overlay["oidc_issuer_url"], "sub": "bare-subject-123"}
        )
        assert user.username == "bare-subject-123"

    def test_provision_user_missing_group_claim_is_not_superuser(
        self,
        db_session: Session,
    ) -> None:
        _enable_oidc()
        user = oidc.provision_user(
            db_session,
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "no-groups",
                "preferred_username": "plainuser",
            },
        )
        assert user.is_superuser is False

    def test_provision_user_rejects_inactive_existing_user(
        self, db_session: Session
    ) -> None:
        _enable_oidc()
        existing = build_user(
            db_session,
            "deactivated",
            active=False,
            password_hash="!oidc:x",
            email="deactivated@example.test",
            oidc_issuer=_overlay["oidc_issuer_url"],
            oidc_subject="deactivated-subject",
            oidc_managed=True,
        )
        db_session.add(existing)
        db_session.commit()

        with pytest.raises(oidc.OIDCError, match="oidc_user_inactive"):
            oidc.provision_user(
                db_session,
                {
                    "iss": _overlay["oidc_issuer_url"],
                    "sub": "deactivated-subject",
                    "groups": [],
                },
            )

    def test_provision_updates_an_existing_active_user(
        self,
        db_session: Session,
    ) -> None:
        _enable_oidc()
        existing = build_user(
            db_session,
            "returning",
            password_hash="!oidc:x",
            email="old@example.test",
            oidc_issuer=_overlay["oidc_issuer_url"],
            oidc_subject="returning-subject",
            oidc_managed=True,
        )
        db_session.add(existing)
        db_session.commit()

        updated = oidc.provision_user(
            db_session,
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "returning-subject",
                "email": "new@example.test",
                "groups": ["vault-admins"],
            },
        )

        assert updated.id == existing.id
        assert updated.email == "new@example.test"
        assert updated.is_superuser is True


class TestOidc:
    def test_superuser_can_configure_oidc_without_secret_disclosure(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={
                "oidc_enabled": True,
                "oidc_issuer_url": "https://auth.example.test/application/o/printstash",
                "oidc_client_id": "printstash",
                "oidc_client_secret": "super-secret-client-value",
                "oidc_scopes": "openid profile email groups",
                "oidc_username_claim": "preferred_username",
                "oidc_groups_claim": "groups",
                "oidc_admin_groups": "vault-admins",
                "oidc_display_name": "Authentik",
                "oidc_redirect_uri": "https://stash.example.test/api/v1/auth/oidc/callback",
                "oidc_allow_insecure_http": False,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["oidc_enabled"] is True
        assert body["oidc_display_name"] == "Authentik"
        assert body["has_oidc_client_secret"] is True
        assert "super-secret-client-value" not in response.text
        assert client.get("/api/v1/auth/providers").json() == {
            "oidc_enabled": True,
            "oidc_display_name": "Authentik",
        }

        from app.db.models import SystemConfig

        stored = db_session.get(SystemConfig, 1)
        assert stored is not None
        assert stored.oidc_client_secret == "super-secret-client-value"
        raw_secret = (
            db_session.connection()
            .exec_driver_sql(
                "SELECT oidc_client_secret FROM system_config WHERE id = 1"
            )
            .scalar_one()
        )
        assert "super-secret-client-value" not in raw_secret

        disabled = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={"oidc_enabled": False},
        )
        assert disabled.status_code == 200
        assert client.get("/api/v1/auth/providers").json()["oidc_enabled"] is False

    def test_oidc_rejects_symmetric_id_token_algorithms(self) -> None:
        token = jwt.encode(
            {"sub": "attacker"},
            "shared-secret-that-must-not-be-trusted",
            algorithm="HS256",
            headers={"kid": "symmetric"},
        )
        jwks = {
            "keys": [
                {
                    "kty": "oct",
                    "kid": "symmetric",
                    "k": "c2hhcmVkLXNlY3JldC10aGF0LW11c3Qtbm90LWJlLXRydXN0ZWQ",
                    "alg": "HS256",
                }
            ]
        }

        with pytest.raises(oidc.OIDCError, match="oidc_invalid_id_token_algorithm"):
            oidc._signing_key(jwks, token)  # noqa: SLF001 - security contract

    def test_oidc_state_cookies_are_secure_for_https_callback(
        self, client: TestClient, monkeypatch
    ) -> None:
        _enable_oidc()
        _overlay["oidc_redirect_uri"] = (
            "https://stash.example.test/api/v1/auth/oidc/callback"
        )

        async def begin_login(_redirect_uri: str) -> oidc.OIDCLogin:
            return oidc.OIDCLogin(
                "https://id.example.test/authorize", "state", "nonce", "verifier"
            )

        monkeypatch.setattr(oidc, "begin_login", begin_login)
        response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)

        cookies = response.headers.get_list("set-cookie")
        assert len(cookies) == 3
        assert all("Secure" in cookie for cookie in cookies)

    def test_the_callback_signs_in_a_newly_provisioned_admin(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch,
    ) -> None:
        _enable_oidc()

        async def begin_login(_redirect_uri: str) -> oidc.OIDCLogin:
            return oidc.OIDCLogin(
                authorization_url="https://id.example.test/authorize",
                state="expected-state",
                nonce="expected-nonce",
                code_verifier="expected-verifier",
            )

        async def exchange_code(
            code: str, redirect_uri: str, verifier: str, nonce: str
        ) -> dict:
            assert code == "provider-code"
            assert redirect_uri.endswith("/api/v1/auth/oidc/callback")
            assert verifier == "expected-verifier"
            assert nonce == "expected-nonce"
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "authentik-user-42",
                "preferred_username": "julia",
                "email": "julia@example.test",
                "groups": ["vault-admins"],
            }

        monkeypatch.setattr(oidc, "begin_login", begin_login)
        monkeypatch.setattr(oidc, "exchange_code", exchange_code)

        start = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
        assert start.status_code == 302
        callback = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=expected-state",
            follow_redirects=False,
        )

        assert callback.status_code == 302
        assert callback.headers["location"] == "/login?oidc=success"
        assert "printstash_session=" in callback.headers["set-cookie"]
        user = db_session.exec(
            select(User).where(User.oidc_subject == "authentik-user-42")
        ).one()
        assert user.username == "julia"
        assert user.oidc_managed is True
        assert user.is_superuser is True
        assert client.get("/api/v1/auth/me").json()["username"] == "julia"
        assert db_session.exec(select(RefreshToken)).all() == []
        assert "Max-Age=" in callback.headers["set-cookie"]

    def test_oidc_callback_does_not_provision_during_restore(
        self,
        client: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_oidc()

        async def begin_login(_redirect_uri: str) -> oidc.OIDCLogin:
            return oidc.OIDCLogin(
                authorization_url="https://id.example.test/authorize",
                state="restore-state",
                nonce="restore-nonce",
                code_verifier="restore-verifier",
            )

        async def exchange_code(*_args) -> dict:
            return {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "must-not-be-created",
                "preferred_username": "restore-racer",
            }

        monkeypatch.setattr(oidc, "begin_login", begin_login)
        monkeypatch.setattr(oidc, "exchange_code", exchange_code)
        monkeypatch.setattr(
            "app.api.v1.auth.begin_mutating_operation", lambda: False, raising=False
        )

        client.get("/api/v1/auth/oidc/login", follow_redirects=False)
        callback = client.get(
            "/api/v1/auth/oidc/callback?code=provider-code&state=restore-state",
            follow_redirects=False,
        )

        assert callback.status_code == 302
        assert callback.headers["location"] == "/login?oidc_error=restore_in_progress"
        assert (
            db_session.exec(
                select(User).where(User.oidc_subject == "must-not-be-created")
            ).first()
            is None
        )

    def test_oidc_jit_does_not_link_colliding_local_username(
        self,
        db_session: Session,
    ) -> None:
        _enable_oidc()
        local = build_user(
            db_session,
            "julia",
            password_hash="local-password-hash",
            email="local@example.test",
        )
        db_session.add(local)
        db_session.commit()

        external = oidc.provision_user(
            db_session,
            {
                "iss": _overlay["oidc_issuer_url"],
                "sub": "different-person",
                "preferred_username": "julia",
                "email": "external@example.test",
                "groups": [],
            },
        )

        assert external.id != local.id
        assert external.username == "julia-2"
        assert local.oidc_subject is None

    def test_oidc_callback_rejects_state_mismatch(
        self, client: TestClient, monkeypatch
    ) -> None:
        _enable_oidc()

        async def begin_login(_redirect_uri: str) -> oidc.OIDCLogin:
            return oidc.OIDCLogin(
                "https://id.example.test/authorize", "right", "nonce", "verifier"
            )

        monkeypatch.setattr(oidc, "begin_login", begin_login)
        client.get("/api/v1/auth/oidc/login", follow_redirects=False)
        response = client.get(
            "/api/v1/auth/oidc/callback?code=code&state=wrong",
            follow_redirects=False,
        )
        assert response.headers["location"] == "/login?oidc_error=invalid_state"
        assert db_session_count_users(client) == 0

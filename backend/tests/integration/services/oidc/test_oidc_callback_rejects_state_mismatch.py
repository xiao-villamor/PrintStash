"""Defends oidc callback rejects state mismatch at the services oidc integration boundary.

A regression could authenticate the wrong identity or accept a forged callback state.
"""

from __future__ import annotations

from ._oidc_shared import (
    Session,
    TestClient,
    User,
    _enable_oidc,
    _overlay,
    asyncio,
    base64,
    datetime,
    db_session_count_users,
    json,
    jwt,
    oidc,
    pytest,
    rsa,
    timedelta,
    timezone,
)


def test_oidc_callback_rejects_state_mismatch(client: TestClient, monkeypatch) -> None:
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


def test_issuer_rejects_non_https_scheme_by_default() -> None:
    _enable_oidc()
    _overlay["oidc_issuer_url"] = "http://id.example.test/application/o/printstash"

    with pytest.raises(oidc.OIDCError, match="oidc_invalid_issuer"):
        oidc._issuer()  # noqa: SLF001


def test_issuer_allows_http_when_explicitly_permitted() -> None:
    _enable_oidc()
    _overlay["oidc_issuer_url"] = "http://id.example.test/application/o/printstash"
    _overlay["oidc_allow_insecure_http"] = True

    assert oidc._issuer() == "http://id.example.test/application/o/printstash"  # noqa: SLF001


def test_begin_login_raises_when_not_configured() -> None:
    _overlay["oidc_enabled"] = False

    with pytest.raises(oidc.OIDCError, match="oidc_not_configured"):
        asyncio.run(oidc.begin_login("https://stash.example.test/callback"))


def test_discovery_wraps_network_failure(monkeypatch) -> None:
    _enable_oidc()

    async def get_json(_url: str) -> dict:
        raise ConnectionError("dns lookup failed")

    monkeypatch.setattr(oidc, "_get_json", get_json)

    with pytest.raises(oidc.OIDCError, match="oidc_discovery_failed"):
        asyncio.run(oidc._discovery())  # noqa: SLF001


def test_discovery_rejects_issuer_mismatch(monkeypatch) -> None:
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


def test_discovery_rejects_document_missing_required_endpoints(monkeypatch) -> None:
    _enable_oidc()

    async def get_json(_url: str) -> dict:
        return {
            "issuer": _overlay["oidc_issuer_url"],
            "authorization_endpoint": "https://id.example.test/authorize",
        }

    monkeypatch.setattr(oidc, "_get_json", get_json)

    with pytest.raises(oidc.OIDCError, match="oidc_invalid_discovery"):
        asyncio.run(oidc._discovery())  # noqa: SLF001


def test_discovery_rejects_insecure_endpoints(monkeypatch) -> None:
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


def test_get_json_raises_on_non_dict_response(monkeypatch) -> None:
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


def test_post_token_raises_on_non_dict_response(monkeypatch) -> None:
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


def test_signing_key_raises_when_jwks_keys_not_a_list() -> None:
    # _signing_key only reads the unverified header, so a hand-built token
    # (valid base64url structure, no real signature) is enough — the
    # algorithm check happens before any signature is ever checked.
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "kid": "a"}).encode()
    ).rstrip(b"=")
    fake_token = f"{header.decode()}.e30.sig"

    with pytest.raises(oidc.OIDCError, match="oidc_invalid_jwks"):
        oidc._signing_key({"keys": "not-a-list"}, fake_token)  # noqa: SLF001


def test_signing_key_raises_when_no_matching_kid() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    public_jwk["kid"] = "the-real-key"
    token = jwt.encode(
        {"sub": "x"}, private_key, algorithm="RS256", headers={"kid": "some-other-kid"}
    )

    with pytest.raises(oidc.OIDCError, match="oidc_signing_key_not_found"):
        oidc._signing_key({"keys": [public_jwk]}, token)  # noqa: SLF001


def test_signing_key_skips_key_that_fails_to_parse() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    public_jwk["kid"] = "matching-kid"
    token = jwt.encode(
        {"sub": "x"}, private_key, algorithm="RS256", headers={"kid": "matching-kid"}
    )
    broken_jwk = {
        "kty": "RSA",
        "kid": "matching-kid",
        "n": "not-valid-base64!!",
        "e": "AQAB",
    }

    key = oidc._signing_key({"keys": [broken_jwk, public_jwk]}, token)  # noqa: SLF001
    assert key is not None


def test_exchange_code_raises_when_id_token_missing(monkeypatch) -> None:
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


def test_exchange_code_raises_on_expired_token(monkeypatch) -> None:
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


def test_exchange_code_wraps_unexpected_failure(monkeypatch) -> None:
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


def test_provision_user_rejects_issuer_mismatch(db_session: Session) -> None:
    _enable_oidc()
    with pytest.raises(oidc.OIDCError, match="oidc_invalid_identity"):
        oidc.provision_user(
            db_session,
            {"iss": "https://someone-elses-idp.example.test", "sub": "x"},
        )


def test_provision_user_rejects_missing_subject(db_session: Session) -> None:
    _enable_oidc()
    with pytest.raises(oidc.OIDCError, match="oidc_invalid_identity"):
        oidc.provision_user(db_session, {"iss": _overlay["oidc_issuer_url"], "sub": ""})


def test_provision_user_without_username_claim_falls_back_to_email_local_part(
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
    db_session: Session,
) -> None:
    _enable_oidc()
    user = oidc.provision_user(
        db_session, {"iss": _overlay["oidc_issuer_url"], "sub": "bare-subject-123"}
    )
    assert user.username == "bare-subject-123"


def test_provision_user_missing_group_claim_is_not_superuser(
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


def test_provision_user_rejects_inactive_existing_user(db_session: Session) -> None:
    _enable_oidc()
    existing = User(
        username="deactivated",
        email="deactivated@example.test",
        hashed_password="!oidc:x",
        is_active=False,
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


def test_provision_user_updates_email_and_superuser_on_existing_active_user(
    db_session: Session,
) -> None:
    _enable_oidc()
    existing = User(
        username="returning",
        email="old@example.test",
        hashed_password="!oidc:x",
        is_active=True,
        is_superuser=False,
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

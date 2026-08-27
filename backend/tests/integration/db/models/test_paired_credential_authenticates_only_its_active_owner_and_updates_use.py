"""Defends paired credential authenticates only its active owner and updates use at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._browser_pairing_api_shared import (
    BrowserDevice,
    HTTPException,
    Session,
    TestClient,
    User,
    _claim_limit,
    _headers,
    col,
    hashlib,
    inbox,
    pytest,
    require_browser_import_user,
    select,
)


def test_paired_credential_authenticates_only_its_active_owner_and_updates_use(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "device-owner")
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    credential = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": code, "name": "Device auth Firefox"},
    ).json()["credential"]

    owner = require_browser_import_user(credential, db_session)
    assert owner.username == "device-owner"
    device = db_session.exec(
        select(BrowserDevice).where(BrowserDevice.name == "Device auth Firefox")
    ).one()
    assert device.last_used_at is not None
    assert credential not in repr(device)
    assert credential not in device.credential_hash

    device.revoked_at = device.created_at
    db_session.commit()
    try:
        require_browser_import_user(credential, db_session)
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "invalid_browser_credential"
    else:
        raise AssertionError("revoked browser credential was accepted")


def test_paired_credential_rejects_invalid_value_with_a_stable_redacted_error(
    db_session: Session,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_browser_import_user("not-a-browser-credential", db_session)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_browser_credential"


def test_browser_credential_is_confined_to_import_routes(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "confined-device-owner")
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    credential = client.post(
        "/api/v1/browser-pairings/claim",
        json={"code": code, "name": "Confined browser"},
    ).json()["credential"]
    device_headers = {"Authorization": f"Bearer {credential}"}

    assert client.get("/api/v1/auth/me", headers=device_headers).status_code == 401
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def no_resolve(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", no_resolve)
    assert (
        client.post(
            "/api/v1/inbox",
            headers=device_headers,
            json={"url": "https://example.com/model", "title": "Browser capture"},
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/v1/inbox/browser-upload",
            headers=device_headers,
            data={"source_url": "https://makerworld.com/en/models/1"},
            files={
                "file": ("widget.3mf", b"browser-owned", "application/octet-stream")
            },
        ).status_code
        == 201
    )


def test_pairing_device_cap_has_the_same_stable_failure_as_an_invalid_code(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "full-device-owner")
    owner = db_session.exec(
        select(User)
        .where(User.username == "full-device-owner")
        .order_by(col(User.id).desc())
    ).first()
    assert owner is not None
    assert owner.id is not None
    for index in range(10):
        db_session.add(
            BrowserDevice(
                user_id=owner.id,
                name=f"existing device {index}",
                credential_hash=hashlib.sha256(
                    f"existing-{index}".encode()
                ).hexdigest(),
            )
        )
    db_session.commit()
    code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
    response = client.post(
        "/api/v1/browser-pairings/claim", json={"code": code, "name": "one too many"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_or_expired_pairing_code"
    for device in db_session.exec(
        select(BrowserDevice).where(BrowserDevice.user_id == owner.id)
    ):
        db_session.delete(device)
    db_session.commit()


def test_pairing_claim_rate_limit_is_stable_and_does_not_echo_codes(
    client: TestClient,
) -> None:
    _claim_limit.limiter.reset()
    secret = "rate-test-secret"
    try:
        for _ in range(10):
            response = client.post(
                "/api/v1/browser-pairings/claim",
                json={"code": secret, "name": "Rate test"},
            )
            assert response.status_code == 400
            assert response.json()["detail"] == "invalid_or_expired_pairing_code"
            assert secret not in response.text
        limited = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": secret, "name": "Rate test"},
        )
        assert limited.status_code == 429
        assert limited.json()["detail"] == "rate_limited"
        assert secret not in limited.text
    finally:
        _claim_limit.limiter.reset()

"""Authenticating a paired browser — and nothing else in the app.

A browser credential is deliberately *not* a login. It is accepted only by the two
importer routes that opt into it, so a stolen credential can add to somebody's inbox but
cannot read their library, change their settings, or see their account. That confinement
is asserted here against the real router, not inferred from the dependency list.

Every rejection answers with the same `invalid_browser_credential`: unknown, revoked, and
owned-by-a-deactivated-user must be indistinguishable, or the error becomes an oracle for
which credentials exist.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.browser_device_auth import (
    require_browser_import_user,
    require_user_or_browser_import_user,
)
from app.core.time import utcnow
from app.db.models import BrowserDevice
from app.services import inbox


@pytest.fixture
def paired(client: TestClient, user_headers):
    """Pair a browser for a fresh user and hand back its credential."""

    def run(username: str, name: str = "Paired browser") -> str:
        headers = user_headers(username)
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        claimed = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": name}
        )
        assert claimed.status_code == 200, claimed.text
        return claimed.json()["credential"]

    return run


@pytest.fixture
def importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let an inbox capture land without reaching out to the source URL."""
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def no_resolve(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", no_resolve)


class TestRequireBrowserImportUser:
    def test_returns_the_account_the_browser_was_paired_with(
        self, db_session: Session, paired
    ) -> None:
        credential = paired("device-owner")

        user = require_browser_import_user(credential, db_session)

        assert user.username == "device-owner"

    def test_records_when_the_credential_was_last_used(
        self, db_session: Session, paired
    ) -> None:
        credential = paired("device-last-used")

        require_browser_import_user(credential, db_session)

        assert db_session.exec(select(BrowserDevice)).one().last_used_at is not None

    def test_rejects_a_value_that_matches_no_device(self, db_session: Session) -> None:
        with pytest.raises(HTTPException) as exc_info:
            require_browser_import_user("not-a-browser-credential", db_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "invalid_browser_credential"

    def test_rejects_an_empty_credential(self, db_session: Session) -> None:
        with pytest.raises(HTTPException) as exc_info:
            require_browser_import_user(None, db_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "invalid_browser_credential"

    def test_rejects_a_revoked_devices_credential(
        self, db_session: Session, paired
    ) -> None:
        credential = paired("device-revoked")
        device = db_session.exec(select(BrowserDevice)).one()
        device.revoked_at = utcnow()
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            require_browser_import_user(credential, db_session)

        assert exc_info.value.detail == "invalid_browser_credential"

    def test_rejects_a_credential_whose_owner_was_deactivated(
        self, db_session: Session, paired
    ) -> None:
        from app.db.models import User

        credential = paired("device-deactivated")
        owner = db_session.exec(
            select(User).where(User.username == "device-deactivated")
        ).one()
        owner.is_active = False
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            require_browser_import_user(credential, db_session)

        # Same message as an unknown credential: no oracle.
        assert exc_info.value.detail == "invalid_browser_credential"


class TestRequireUserOrBrowserImportUser:
    def test_accepts_a_write_scoped_login(
        self, db_session: Session, make_user, headers_for
    ) -> None:
        owner = make_user("dual-auth-jwt")
        token = headers_for(owner)["Authorization"].removeprefix("Bearer ")

        assert (
            require_user_or_browser_import_user(token, db_session).username
            == "dual-auth-jwt"
        )

    def test_accepts_a_paired_browser(self, db_session: Session, paired) -> None:
        credential = paired("dual-auth-device")

        assert (
            require_user_or_browser_import_user(credential, db_session).username
            == "dual-auth-device"
        )

    def test_rejects_a_read_scoped_login(
        self, db_session: Session, make_user, headers_for
    ) -> None:
        owner = make_user("dual-auth-read")
        token = headers_for(owner, scope="read")["Authorization"].removeprefix(
            "Bearer "
        )

        with pytest.raises(HTTPException) as exc_info:
            require_user_or_browser_import_user(token, db_session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "not_authenticated"

    def test_rejects_a_malformed_token_without_falling_back_to_a_device(
        self, db_session: Session
    ) -> None:
        with pytest.raises(HTTPException) as exc_info:
            require_user_or_browser_import_user("not.a.jwt", db_session)

        # Three segments means "this is a JWT"; it must not reach the device path.
        assert exc_info.value.detail == "not_authenticated"

    def test_rejects_a_token_whose_subject_is_not_an_account_id(
        self, db_session: Session
    ) -> None:
        from app.services.auth import create_access_token

        # A well-signed token can still carry a subject this deployment never
        # issued — a legacy or tampered payload must not reach a user lookup.
        forged = create_access_token("not-an-id", "ghost", scope="write")  # type: ignore[arg-type]

        with pytest.raises(HTTPException) as exc_info:
            require_user_or_browser_import_user(forged, db_session)

        assert exc_info.value.detail == "not_authenticated"


class TestBrowserCredentialConfinement:
    def test_a_paired_browser_can_capture_a_url(
        self, client: TestClient, paired, importable
    ) -> None:
        credential = paired("confined-capture")

        response = client.post(
            "/api/v1/inbox",
            headers={"Authorization": f"Bearer {credential}"},
            json={"url": "https://example.com/model", "title": "Browser capture"},
        )

        assert response.status_code == 202, response.text

    def test_a_paired_browser_can_upload_a_captured_file(
        self, client: TestClient, paired, importable
    ) -> None:
        credential = paired("confined-upload")

        response = client.post(
            "/api/v1/inbox/browser-upload",
            headers={"Authorization": f"Bearer {credential}"},
            data={"source_url": "https://makerworld.com/en/models/1"},
            files={
                "file": ("widget.3mf", b"browser-owned", "application/octet-stream")
            },
        )

        assert response.status_code == 201, response.text

    def test_a_paired_browser_cannot_read_the_account(
        self, client: TestClient, paired
    ) -> None:
        credential = paired("confined-account")

        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {credential}"}
        )

        assert response.status_code == 401, response.text

    def test_a_paired_browser_cannot_read_the_library(
        self, client: TestClient, paired
    ) -> None:
        credential = paired("confined-library")

        response = client.get(
            "/api/v1/models", headers={"Authorization": f"Bearer {credential}"}
        )

        assert response.status_code == 401, response.text

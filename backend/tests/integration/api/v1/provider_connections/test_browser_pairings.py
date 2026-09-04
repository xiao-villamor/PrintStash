"""Pairing a browser extension with an account, and managing what has been paired.

The pairing code is the whole security boundary: `POST /browser-pairings/claim` needs no
login, so anyone who can guess a live code gets a credential for somebody's library.
Four rules hold that line and each one has a test here — a code is **single-use**, it
**expires in five minutes**, it **locks after five live failures**, and the claim endpoint
is **rate limited**. All four failures answer with the same opaque `invalid_or_expired_
pairing_code`, and none of them ever echo the code back: a response that distinguished
"wrong" from "expired" from "capped" would be an oracle.

The credential itself is shown exactly once, at claim time. Only its hash is stored, and
no listing ever returns it.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import BrowserDevice, BrowserPairingCode

DEVICE_CAP = 10


def _fill_device_cap(session: Session, user_id: int) -> None:
    for index in range(DEVICE_CAP):
        session.add(
            BrowserDevice(
                user_id=user_id,
                name=f"existing device {index}",
                credential_hash=hashlib.sha256(
                    f"existing-{index}".encode()
                ).hexdigest(),
            )
        )
    session.commit()


class TestCreatePairing:
    def test_hands_back_a_pairing_code_with_its_expiry(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.post(
            "/api/v1/browser-pairings", headers=user_headers("pairing-create")
        )

        assert response.status_code == 201, response.text
        assert response.json()["code"]
        assert response.json()["expires_at"]

    def test_gives_the_code_five_minutes_to_live(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        client.post("/api/v1/browser-pairings", headers=user_headers("pairing-ttl"))

        row = db_session.exec(select(BrowserPairingCode)).one()
        # `created_at` and `expires_at` are two separate clock reads.
        assert abs(
            (row.expires_at - row.created_at) - timedelta(minutes=5)
        ) < timedelta(seconds=1)

    def test_stores_only_a_hash_of_the_code(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        code = client.post(
            "/api/v1/browser-pairings", headers=user_headers("pairing-hashed")
        ).json()["code"]

        row = db_session.exec(select(BrowserPairingCode)).one()
        assert row.code_hash == hashlib.sha256(code.encode()).hexdigest()
        assert code not in repr(row)

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.post("/api/v1/browser-pairings").status_code == 401


class TestClaimPairing:
    def test_hands_back_a_credential_with_its_device(
        self, client: TestClient, user_headers
    ) -> None:
        code = client.post(
            "/api/v1/browser-pairings", headers=user_headers("claim-happy")
        ).json()["code"]

        response = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Firefox"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["credential"]
        assert response.json()["device"]["name"] == "Firefox"

    def test_stores_only_a_hash_of_the_credential(
        self, client: TestClient, db_session: Session, user_headers, pair
    ) -> None:
        credential, _ = pair(user_headers("claim-hashed"), "Firefox")

        device = db_session.exec(select(BrowserDevice)).one()
        assert device.credential_hash != credential
        assert credential not in repr(device)

    def test_spends_the_code(
        self, client: TestClient, db_session: Session, user_headers, pair
    ) -> None:
        pair(user_headers("claim-spends"), "Firefox")

        assert db_session.exec(select(BrowserPairingCode)).one().used_at is not None

    def test_refuses_a_code_that_was_already_claimed(
        self, client: TestClient, user_headers
    ) -> None:
        headers = user_headers("claim-replay")
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "First"}
        )

        replay = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Replay"}
        )

        assert replay.status_code == 400, replay.text
        assert replay.json()["detail"] == "invalid_or_expired_pairing_code"

    def test_reuses_the_row_when_a_revoked_device_is_paired_again(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("claim-repair")
        _, device = pair(headers, "Default browser")
        client.delete(f"/api/v1/browser-pairings/{device['id']}", headers=headers)

        _, repaired = pair(headers, "Default browser")

        assert repaired["id"] == device["id"]
        assert repaired["revoked_at"] is None

    def test_issues_a_new_credential_when_a_revoked_device_is_paired_again(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("claim-repair-credential")
        old_credential, device = pair(headers, "Default browser")
        client.delete(f"/api/v1/browser-pairings/{device['id']}", headers=headers)

        new_credential, _ = pair(headers, "Default browser")

        assert new_credential != old_credential

    def test_refuses_a_name_an_active_device_already_uses(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("claim-duplicate-name")
        pair(headers, "Default browser")
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]

        conflict = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "Default browser"},
        )

        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"] == "browser_device_name_in_use"

    def test_leaves_the_code_claimable_after_a_name_conflict(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("claim-conflict-retry")
        pair(headers, "Default browser")
        code = client.post("/api/v1/browser-pairings", headers=headers).json()["code"]
        client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "Default browser"},
        )

        retry = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "Different browser"},
        )

        # A typo'd name must not burn the code the user is holding.
        assert retry.status_code == 200, retry.text

    def test_refuses_a_code_this_deployment_never_issued(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": "not-a-real-pairing-code", "name": "Forged"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_or_expired_pairing_code"

    def test_refuses_a_code_that_has_expired(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        code = client.post(
            "/api/v1/browser-pairings", headers=user_headers("claim-expired")
        ).json()["code"]
        row = db_session.exec(select(BrowserPairingCode)).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()

        response = client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Expired"}
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_or_expired_pairing_code"

    def test_spends_no_attempt_on_a_code_that_has_expired(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        code = client.post(
            "/api/v1/browser-pairings", headers=user_headers("claim-expired-attempts")
        ).json()["code"]
        row = db_session.exec(select(BrowserPairingCode)).one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db_session.commit()

        client.post(
            "/api/v1/browser-pairings/claim", json={"code": code, "name": "Expired"}
        )

        db_session.refresh(row)
        assert row.attempts == 0

    def test_spends_no_attempt_on_a_code_that_does_not_match(
        self, client: TestClient, db_session: Session, user_headers
    ) -> None:
        code = client.post(
            "/api/v1/browser-pairings", headers=user_headers("claim-wrong-secret")
        ).json()["code"]
        row = db_session.exec(select(BrowserPairingCode)).one()

        client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": f"wrong-{code}", "name": "Guess"},
        )

        # Only a *live* code's failures count toward the lock, or an attacker
        # could exhaust somebody else's code by guessing.
        db_session.refresh(row)
        assert row.attempts == 0

    def test_refuses_a_claim_that_would_pass_the_device_cap(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        user = make_user("claim-capped")
        assert user.id is not None
        _fill_device_cap(db_session, user.id)
        code = client.post(
            "/api/v1/browser-pairings", headers=headers_for(user)
        ).json()["code"]

        response = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "One too many"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_or_expired_pairing_code"

    def test_locks_a_code_after_five_live_failures(
        self, client: TestClient, db_session: Session, make_user, headers_for
    ) -> None:
        user = make_user("claim-locked")
        assert user.id is not None
        _fill_device_cap(db_session, user.id)
        code = client.post(
            "/api/v1/browser-pairings", headers=headers_for(user)
        ).json()["code"]
        for _ in range(5):
            client.post(
                "/api/v1/browser-pairings/claim",
                json={"code": code, "name": "blocked-browser"},
            )
        for device in db_session.exec(select(BrowserDevice)):
            db_session.delete(device)
        db_session.commit()

        response = client.post(
            "/api/v1/browser-pairings/claim",
            json={"code": code, "name": "after-the-cap-cleared"},
        )

        # The five failures burned the code even though the cap is no longer hit.
        assert response.status_code == 400, response.text
        assert db_session.exec(select(BrowserPairingCode)).one().attempts == 5

    def test_rate_limits_a_caller_guessing_codes(self, client: TestClient) -> None:
        secret = "rate-test-secret"
        for _ in range(10):
            assert (
                client.post(
                    "/api/v1/browser-pairings/claim",
                    json={"code": secret, "name": "Rate test"},
                ).status_code
                == 400
            )

        limited = client.post(
            "/api/v1/browser-pairings/claim", json={"code": secret, "name": "Rate test"}
        )

        assert limited.status_code == 429, limited.text
        assert limited.json()["detail"] == "rate_limited"

    def test_never_echoes_the_code_it_rejected(self, client: TestClient) -> None:
        secret = "echo-test-secret"

        response = client.post(
            "/api/v1/browser-pairings/claim", json={"code": secret, "name": "Echo test"}
        )

        assert secret not in response.text

    def test_rejects_a_code_shorter_than_any_it_issues(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/browser-pairings/claim", json={"code": "short", "name": "Tiny"}
        )

        assert response.status_code == 422, response.text


class TestListDevices:
    def test_lists_the_callers_devices_oldest_first(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("list-devices")
        pair(headers, "First browser")
        pair(headers, "Second browser")

        body = client.get("/api/v1/browser-pairings", headers=headers).json()

        assert [row["name"] for row in body] == ["First browser", "Second browser"]

    def test_never_returns_the_credential(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("list-devices-secret")
        credential, _ = pair(headers, "Firefox")

        listing = client.get("/api/v1/browser-pairings", headers=headers)

        assert credential not in listing.text

    def test_hides_another_accounts_devices(
        self, client: TestClient, user_headers, pair
    ) -> None:
        pair(user_headers("device-owner"), "Owner browser")

        body = client.get(
            "/api/v1/browser-pairings", headers=user_headers("device-stranger")
        ).json()

        assert body == []

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/browser-pairings").status_code == 401


class TestRenameDevice:
    def test_renames_the_device(self, client: TestClient, user_headers, pair) -> None:
        headers = user_headers("rename-device")
        _, device = pair(headers, "Old name")

        response = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers=headers,
            json={"name": "New name"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "New name"

    def test_keeps_the_credential_working_across_a_rename(
        self, client: TestClient, db_session: Session, user_headers, pair
    ) -> None:
        from app.core.browser_device_auth import require_browser_import_user

        headers = user_headers("rename-keeps-credential")
        credential, device = pair(headers, "Old name")

        client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers=headers,
            json={"name": "New name"},
        )

        assert (
            require_browser_import_user(credential, db_session).username
            == "rename-keeps-credential"
        )

    def test_refuses_to_rename_another_accounts_device(
        self, client: TestClient, user_headers, pair
    ) -> None:
        _, device = pair(user_headers("rename-owner"), "Owner browser")

        response = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers=user_headers("rename-stranger"),
            json={"name": "Stolen"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "browser_device_not_found"

    def test_reports_a_device_that_does_not_exist(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.patch(
            "/api/v1/browser-pairings/9999",
            headers=user_headers("rename-missing"),
            json={"name": "Ghost"},
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "browser_device_not_found"

    def test_rejects_an_empty_name(
        self, client: TestClient, user_headers, pair
    ) -> None:
        headers = user_headers("rename-empty")
        _, device = pair(headers, "Named")

        response = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers=headers,
            json={"name": ""},
        )

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, user_headers, pair
    ) -> None:
        _, device = pair(user_headers("rename-anon"), "Named")

        response = client.patch(
            f"/api/v1/browser-pairings/{device['id']}", json={"name": "Anonymous"}
        )

        assert response.status_code == 401, response.text


class TestRevokeDevice:
    def test_revokes_the_device(
        self, client: TestClient, db_session: Session, user_headers, pair
    ) -> None:
        headers = user_headers("revoke-device")
        _, device = pair(headers, "Doomed browser")

        response = client.delete(
            f"/api/v1/browser-pairings/{device['id']}", headers=headers
        )

        assert response.status_code == 204, response.text
        assert db_session.exec(select(BrowserDevice)).one().revoked_at is not None

    def test_stops_the_credential_from_authenticating(
        self, client: TestClient, db_session: Session, user_headers, pair
    ) -> None:
        import pytest
        from fastapi import HTTPException

        from app.core.browser_device_auth import require_browser_import_user

        headers = user_headers("revoke-credential")
        credential, device = pair(headers, "Doomed browser")

        client.delete(f"/api/v1/browser-pairings/{device['id']}", headers=headers)

        with pytest.raises(HTTPException) as exc_info:
            require_browser_import_user(credential, db_session)
        assert exc_info.value.status_code == 401

    def test_refuses_to_revoke_another_accounts_device(
        self, client: TestClient, user_headers, pair
    ) -> None:
        _, device = pair(user_headers("revoke-owner"), "Owner browser")

        response = client.delete(
            f"/api/v1/browser-pairings/{device['id']}",
            headers=user_headers("revoke-stranger"),
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "browser_device_not_found"

    def test_reports_a_device_that_does_not_exist(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.delete(
            "/api/v1/browser-pairings/9999", headers=user_headers("revoke-missing")
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, user_headers, pair
    ) -> None:
        _, device = pair(user_headers("revoke-anon"), "Named")

        assert (
            client.delete(f"/api/v1/browser-pairings/{device['id']}").status_code == 401
        )

"""Browser-name drafts are conditional on account, pairing incarnation and history.

Pairing remains compatible with existing extensions. Last-use telemetry cannot
invalidate an editor; a rename, revocation or replacement credential must.
"""

import pytest
from sqlalchemy import update
from sqlmodel import col, select

from app.core.browser_device_auth import require_browser_import_user
from app.db.models import BrowserDevice, LibraryRevision


def _base(device):
    return {
        "X-PrintStash-Edit-Contract": "conditional-v1",
        "If-Match": f'"browser-device-{device["id"]}-e{device["edit_epoch"]}-v{device["edit_version"]}"',
    }


class TestBrowserEditing:
    def test_publishes_an_opaque_editing_base(
        self, client, user_headers, pair, db_session
    ):
        headers = user_headers("browser-base")
        credential, device = pair(headers, "Browser")
        row = db_session.exec(select(BrowserDevice)).one()
        listing = client.get("/api/v1/browser-pairings", headers=headers)
        body = listing.json()[0]
        assert body["edit_version"] > 0
        assert len(body["edit_epoch"]) == 32
        assert body["edit_epoch"] == device["edit_epoch"]
        assert credential not in listing.text
        assert row.credential_hash not in listing.text

    def test_accepts_the_captured_browser_base(self, client, user_headers, pair):
        headers = user_headers("browser-save")
        _, device = pair(headers, "Browser")
        saved = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, **_base(device)},
            json={"name": "Office"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["name"] == "Office"
        assert saved.json()["edit_epoch"] == device["edit_epoch"]
        assert saved.json()["edit_version"] > device["edit_version"]

    def test_refuses_an_obsolete_browser_base(self, client, user_headers, pair):
        headers = user_headers("browser-conflict")
        _, device = pair(headers, "Browser")
        path = f"/api/v1/browser-pairings/{device['id']}"
        winner = client.patch(
            path, headers={**headers, **_base(device)}, json={"name": "Winner"}
        )
        assert winner.status_code == 200, winner.text
        loser = client.patch(
            path, headers={**headers, **_base(device)}, json={"name": "Loser"}
        )
        assert loser.status_code == 412, loser.text
        assert (
            client.get("/api/v1/browser-pairings", headers=headers).json()[0]["name"]
            == "Winner"
        )

    def test_requires_the_opted_in_browser_base(self, client, user_headers, pair):
        headers = user_headers("browser-required")
        _, device = pair(headers, "Browser")
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
            json={"name": "Lost"},
        )
        assert result.status_code == 428, result.text
        assert (
            client.get("/api/v1/browser-pairings", headers=headers).json()[0]["name"]
            == "Browser"
        )

    @pytest.mark.parametrize(
        "precondition,status",
        [
            ({"If-Match": "*"}, 412),
            ({"If-Match": '"config-e' + "a" * 32 + '-v1"'}, 412),
            ({"If-Match": '"browser-device-99999-e' + "a" * 32 + '-v1"'}, 412),
            (
                {
                    "If-Match": '"browser-device-1-e'
                    + "a" * 32
                    + '-v99999999999999999999999"'
                },
                412,
            ),
            ({"X-PrintStash-Edit-Contract": "unsupported"}, 400),
        ],
    )
    def test_rejects_malformed_browser_preconditions(
        self, client, user_headers, pair, precondition, status
    ):
        headers = user_headers("browser-malformed")
        _, device = pair(headers, "Browser")
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, **precondition},
            json={"name": "Lost"},
        )
        assert result.status_code == status, result.text
        assert (
            client.get("/api/v1/browser-pairings", headers=headers).json()[0]["name"]
            == "Browser"
        )

    def test_invalidates_drafts_after_legacy_edits(self, client, user_headers, pair):
        headers = user_headers("browser-legacy")
        _, device = pair(headers, "Browser")
        path = f"/api/v1/browser-pairings/{device['id']}"
        assert (
            client.patch(path, headers=headers, json={"name": "Legacy"}).status_code
            == 200
        )
        result = client.patch(
            path, headers={**headers, **_base(device)}, json={"name": "Lost"}
        )
        assert result.status_code == 412, result.text

    def test_ignores_browser_use_telemetry(
        self, client, user_headers, pair, db_session
    ):
        headers = user_headers("browser-telemetry")
        credential, device = pair(headers, "Browser")
        require_browser_import_user(credential, db_session)
        db_session.commit()
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, **_base(device)},
            json={"name": "Office"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["last_used_at"] is not None

    def test_refuses_a_draft_after_re_pairing(self, client, user_headers, pair):
        headers = user_headers("browser-repaired")
        _, device = pair(headers, "Browser")
        path = f"/api/v1/browser-pairings/{device['id']}"
        assert client.delete(path, headers=headers).status_code == 204
        _, replacement = pair(headers, "Browser")
        assert replacement["id"] == device["id"]
        assert replacement["edit_epoch"] != device["edit_epoch"]
        result = client.patch(
            path, headers={**headers, **_base(device)}, json={"name": "Lost"}
        )
        assert result.status_code == 412, result.text
        assert (
            client.get("/api/v1/browser-pairings", headers=headers).json()[0]["name"]
            == "Browser"
        )

    def test_refuses_editing_a_revoked_browser(self, client, user_headers, pair):
        headers = user_headers("browser-revoked")
        _, device = pair(headers, "Browser")
        path = f"/api/v1/browser-pairings/{device['id']}"
        assert client.delete(path, headers=headers).status_code == 204
        revoked = client.get("/api/v1/browser-pairings", headers=headers).json()[0]
        result = client.patch(
            path, headers={**headers, **_base(revoked)}, json={"name": "Lost"}
        )
        assert result.status_code == 409, result.text
        assert (
            client.get("/api/v1/browser-pairings", headers=headers).json()[0]["name"]
            == "Browser"
        )

    def test_reports_a_duplicate_browser_name(self, client, user_headers, pair):
        headers = user_headers("browser-duplicate")
        _, device = pair(headers, "Browser")
        pair(headers, "Office")
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, **_base(device)},
            json={"name": "Office"},
        )
        assert result.status_code == 409, result.text
        assert result.json()["detail"] == "browser_device_name_in_use"
        current = client.get("/api/v1/browser-pairings", headers=headers).json()[0]
        assert current["name"] == device["name"]
        assert current["edit_version"] == device["edit_version"]

    def test_hides_another_accounts_editing_authority(self, client, user_headers, pair):
        _, device = pair(user_headers("browser-owner"), "Browser")
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**user_headers("browser-stranger"), **_base(device)},
            json={"name": "Lost"},
        )
        assert result.status_code == 404, result.text
        assert device["edit_epoch"] not in result.text

    def test_invalidates_drafts_across_restored_history(
        self, client, user_headers, pair, db_session
    ):
        headers = user_headers("browser-history")
        _, device = pair(headers, "Browser")
        db_session.execute(update(LibraryRevision).values(epoch="b" * 32))
        db_session.commit()
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, **_base(device)},
            json={"name": "Lost"},
        )
        assert result.status_code == 412, result.text

    def test_versions_direct_writes(self, client, user_headers, pair, db_session):
        headers = user_headers("browser-direct")
        _, device = pair(headers, "Browser")
        for name in ("Other", "Browser"):
            db_session.execute(
                update(BrowserDevice)
                .where(col(BrowserDevice.id) == device["id"])
                .values(name=name)
            )
            db_session.commit()
        result = client.patch(
            f"/api/v1/browser-pairings/{device['id']}",
            headers={**headers, **_base(device)},
            json={"name": "Lost"},
        )
        assert result.status_code == 412, result.text

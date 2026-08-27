"""Defends share at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._cross_unit_shared import (
    FileRevisionStatus,
    ShareLink,
    _make_file,
    _make_model,
)


class TestShareIsolation:
    def _create_share(self, client, auth_headers, model_id, **body):
        payload = {"expires_in_days": 7, "allow_download": False, **body}
        res = client.post(
            f"/api/v1/models/{model_id}/shares", json=payload, headers=auth_headers
        )
        assert res.status_code == 200, res.text
        return res.json()

    def test_public_view_and_token_only_grants_one_model(
        self, client, db_session, auth_headers
    ):
        shared = _make_model(db_session, slug="shared", hash_="s" * 64)
        _make_file(db_session, shared, filename="shared.stl")
        other = _make_model(db_session, slug="other", hash_="o" * 64)
        other_file = _make_file(db_session, other, filename="secret.stl")

        created = self._create_share(client, auth_headers, shared.id)
        token = created["token"]

        # Public detail works without auth.
        res = client.get(f"/api/v1/share/{token}")
        assert res.status_code == 200
        assert res.json()["name"] == "M"

        # A file from a different model is not reachable through this token.
        res = client.get(f"/api/v1/share/{token}/files/{other_file.id}/stl")
        assert res.status_code == 404

    def test_garbage_and_revoked_tokens_404(self, client, db_session, auth_headers):
        m = _make_model(db_session, slug="rev", hash_="r" * 64)
        created = self._create_share(client, auth_headers, m.id)
        token = created["token"]

        assert client.get("/api/v1/share/not-a-real-token").status_code == 404

        # Revoke → 404.
        client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)
        assert client.get(f"/api/v1/share/{token}").status_code == 404

    def test_expired_token_404(self, client, db_session, auth_headers):
        from datetime import timedelta

        from app.core.time import utcnow

        m = _make_model(db_session, slug="exp", hash_="e" * 64)
        created = self._create_share(client, auth_headers, m.id)
        link = db_session.get(ShareLink, created["id"])
        link.expires_at = utcnow() - timedelta(days=1)
        db_session.add(link)
        db_session.commit()
        assert client.get(f"/api/v1/share/{created['token']}").status_code == 404

    def test_download_blocked_when_view_only(self, client, db_session, auth_headers):
        m = _make_model(db_session, slug="dl", hash_="d" * 64)
        f = _make_file(db_session, m, filename="dl.stl")
        created = self._create_share(client, auth_headers, m.id, allow_download=False)
        res = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert res.status_code == 403

    def test_share_can_scope_to_selected_gcode_revisions(
        self, client, db_session, auth_headers
    ):
        m = _make_model(db_session, slug="scope", hash_="q" * 64)
        mesh = _make_file(db_session, m, filename="part.stl", version=1)
        rev1 = _make_file(
            db_session, m, filename="rev1.gcode", ftype="gcode", version=2
        )
        rev2 = _make_file(
            db_session, m, filename="rev2.gcode", ftype="gcode", version=3
        )
        rev2.revision_label = "PLA fast"
        rev2.revision_status = FileRevisionStatus.KNOWN_GOOD
        db_session.add(rev2)
        db_session.commit()

        created = self._create_share(
            client, auth_headers, m.id, revision_file_ids=[rev2.id]
        )
        res = client.get(f"/api/v1/share/{created['token']}")
        assert res.status_code == 200
        files = res.json()["files"]
        assert {f["id"] for f in files} == {mesh.id, rev2.id}
        shared_rev = next(f for f in files if f["id"] == rev2.id)
        assert shared_rev["gcode_revision_number"] == 2
        assert shared_rev["revision_label"] == "PLA fast"
        assert shared_rev["revision_status"] == "known_good"

        blocked = client.get(f"/api/v1/share/{created['token']}/files/{rev1.id}/gcode")
        assert blocked.status_code == 404

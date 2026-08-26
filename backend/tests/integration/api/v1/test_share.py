"""API tests for /share (public) and admin share management endpoints,
targeting error branches not exercised by
tests/integration/api/v1/models/test_share.py's
TestShareIsolation (missing_blob, no-thumbnail, download-disabled,
gcode wrong-type, model-not-found on admin routes)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import File, Model


def _make_model(db_session: Session, *, name="M", slug="m", hash_="h" * 64) -> Model:
    m = Model(name=name, slug=slug, hash=hash_)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _make_file(
    db_session: Session, model: Model, *, filename="part.stl", ftype="stl", path=None
) -> File:
    f = File(
        model_id=model.id,
        path=path or f"/nonexistent/{filename}",
        original_filename=filename,
        file_type=ftype,
        version=1,
        size_bytes=10,
        sha256="a" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


def _create_share(client: TestClient, auth_headers, model_id: int, **body) -> dict:
    payload = {"expires_in_days": 7, "allow_download": False, **body}
    res = client.post(
        f"/api/v1/models/{model_id}/shares", json=payload, headers=auth_headers
    )
    assert res.status_code == 200, res.text
    return res.json()


class TestSharedThumbnail:
    def test_no_thumbnail_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="no-thumb", hash_="1" * 64)
        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/thumbnail")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_thumbnail_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="has-thumb", hash_="1a" * 32)
        gcode_file = _make_file(db_session, model, filename="x.stl", ftype="stl")
        backend = get_backend()
        backend.write_bytes(b"webp-bytes", backend.thumbnail_key(gcode_file.id))
        model.thumbnail_file_id = gcode_file.id
        db_session.add(model)
        db_session.commit()

        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/thumbnail")
        assert resp.status_code == 200
        assert resp.content == b"webp-bytes"


class TestSharedStl:
    def test_non_mesh_file_type_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="stl-wrong-type", hash_="2" * 64)
        gcode_file = _make_file(db_session, model, filename="x.gcode", ftype="gcode")
        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{gcode_file.id}/stl")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_mesh_file_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="stl-ok", hash_="2a" * 32)
        key = "share-stl-ok.stl"
        get_backend().write_bytes(b"solid stl-bytes endsolid", key)
        stl_file = _make_file(
            db_session, model, filename="part.stl", ftype="stl", path=key
        )
        created = _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{stl_file.id}/stl")
        assert resp.status_code == 200


class TestSharedDownload:
    def test_download_disabled_forbidden(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="dl-disabled", hash_="3" * 64)
        f = _make_file(db_session, model)
        created = _create_share(client, auth_headers, model.id, allow_download=False)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "download_disabled"

    def test_download_missing_blob_410(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="dl-missing-blob", hash_="4" * 64)
        f = _make_file(db_session, model)
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_download_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="dl-ok", hash_="5" * 64)
        key = "share-download-ok.stl"
        get_backend().write_bytes(b"stl-bytes", key)
        f = _make_file(db_session, model, path=key)
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert resp.status_code == 200
        assert resp.content == b"stl-bytes"


class TestSharedGcode:
    def test_view_only_share_cannot_fetch_original_gcode(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="gcode-view-only", hash_="5a" * 32)
        key = "share-gcode-view-only.gcode"
        get_backend().write_bytes(b"G1 X0 Y0\n", key)
        gcode_file = _make_file(
            db_session,
            model,
            filename="private.gcode",
            ftype="gcode",
            path=key,
        )
        created = _create_share(client, auth_headers, model.id, allow_download=False)

        resp = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode_file.id}/gcode"
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "download_disabled"

    def test_wrong_file_type_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="gcode-wrong-type", hash_="6" * 64)
        stl_file = _make_file(db_session, model, filename="x.stl", ftype="stl")
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(f"/api/v1/share/{created['token']}/files/{stl_file.id}/gcode")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_missing_blob_410(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="gcode-missing-blob", hash_="7" * 64)
        gcode_file = _make_file(db_session, model, filename="x.gcode", ftype="gcode")
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode_file.id}/gcode"
        )
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        from app.services.storage_backend import get_backend

        model = _make_model(db_session, slug="gcode-ok", hash_="8" * 64)
        key = "share-gcode-ok.gcode"
        get_backend().write_bytes(b"G1 X0 Y0\n", key)
        gcode_file = _make_file(
            db_session, model, filename="x.gcode", ftype="gcode", path=key
        )
        created = _create_share(client, auth_headers, model.id, allow_download=True)
        resp = client.get(
            f"/api/v1/share/{created['token']}/files/{gcode_file.id}/gcode"
        )
        assert resp.status_code == 200
        assert resp.content == b"G1 X0 Y0\n"


class TestAdminShareManagement:
    def test_create_share_model_not_found(
        self, client: TestClient, auth_headers
    ) -> None:
        resp = client.post(
            "/api/v1/models/999/shares",
            json={"expires_in_days": 7, "allow_download": False},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "model_not_found"

    def test_list_shares_model_not_found(
        self, client: TestClient, auth_headers
    ) -> None:
        resp = client.get("/api/v1/models/999/shares", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "model_not_found"

    def test_list_shares_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="listable", hash_="9" * 64)
        _create_share(client, auth_headers, model.id)
        resp = client.get(f"/api/v1/models/{model.id}/shares", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_revoke_share_not_found(self, client: TestClient, auth_headers) -> None:
        resp = client.delete("/api/v1/shares/999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "share_not_found"

    def test_revoke_share_success(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="revocable", hash_="a1" * 32)
        created = _create_share(client, auth_headers, model.id)
        resp = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["revoked_at"] is not None

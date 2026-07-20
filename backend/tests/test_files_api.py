"""API-level tests for /files — error branches and S3-presigned-URL paths
not exercised by tests/test_new_features.py, tests/test_ingest_api.py, or
tests/test_slicer_download.py."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import File, Model
from app.services.jobs import registry
from app.services.storage_backend import LocalStorageBackend, get_backend


def _make_model(db_session: Session, *, name="M", slug="m", hash_="h" * 64) -> Model:
    m = Model(name=name, slug=slug, hash=hash_)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _make_file(
    db_session: Session,
    model: Model,
    *,
    filename="part.stl",
    ftype="stl",
    path=None,
    sha256="a" * 64,
) -> File:
    f = File(
        model_id=model.id,
        path=path or f"/nonexistent/{filename}",
        original_filename=filename,
        file_type=ftype,
        version=1,
        size_bytes=10,
        sha256=sha256,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


class TestLiveFile:
    def test_deleted_model_hides_file(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="soon-gone", hash_="1" * 64)
        f = _make_file(db_session, model)
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        resp = client.get(f"/api/v1/files/{f.id}/download", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "file_not_found"

    def test_deleted_file_itself_hidden(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="host-2", hash_="2" * 64)
        f = _make_file(db_session, model)
        f.deleted_at = utcnow()
        db_session.add(f)
        db_session.commit()

        resp = client.get(f"/api/v1/files/{f.id}/download", headers=auth_headers)
        assert resp.status_code == 404


class TestServeFileDirectPathMissing:
    def test_direct_path_backend_missing_blob_410(
        self, client: TestClient, db_session: Session, auth_headers, monkeypatch
    ) -> None:
        model = _make_model(db_session, slug="direct-missing", hash_="3" * 64)
        key = "gone-blob.stl"
        f = _make_file(db_session, model, path=key)

        backend = get_backend()
        assert isinstance(backend, LocalStorageBackend)
        # Make backend.exists() lie so _serve_download gets past its own check
        # and _serve_file's direct_path().exists() is what fails (410 at the
        # FileResponse layer, not the earlier existence gate).
        monkeypatch.setattr(backend, "exists", lambda _key: True)

        resp = client.get(f"/api/v1/files/{f.id}/download", headers=auth_headers)
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"


class TestDownloadUrl:
    def test_local_backend_returns_api_url(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="local-url", hash_="4" * 64)
        f = _make_file(db_session, model)
        resp = client.get(f"/api/v1/files/{f.id}/download-url", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["backend"] == "local"
        assert body["url"] == f"/api/v1/files/{f.id}/download"

    def test_s3_backend_returns_presigned_url(
        self, client: TestClient, db_session: Session, auth_headers, monkeypatch
    ) -> None:
        model = _make_model(db_session, slug="s3-url", hash_="5" * 64)
        f = _make_file(db_session, model)
        backend = get_backend()
        monkeypatch.setattr(
            backend, "presigned_download_url", lambda key, filename: "https://s3.example/fake"
        )
        resp = client.get(f"/api/v1/files/{f.id}/download-url", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["backend"] == "s3"
        assert body["url"] == "https://s3.example/fake"
        assert "expires_in" in body


class TestDownloadDirect:
    def test_local_backend_falls_back_to_stream(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="direct-local", hash_="6" * 64)
        key = "direct-local.stl"
        get_backend().write_bytes(b"stl-bytes", key)
        f = _make_file(db_session, model, path=key)
        resp = client.get(
            f"/api/v1/files/{f.id}/download-direct",
            headers=auth_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert resp.content == b"stl-bytes"

    def test_s3_backend_redirects(
        self, client: TestClient, db_session: Session, auth_headers, monkeypatch
    ) -> None:
        model = _make_model(db_session, slug="direct-s3", hash_="7" * 64)
        f = _make_file(db_session, model)
        backend = get_backend()
        monkeypatch.setattr(
            backend, "presigned_download_url", lambda key, filename: "https://s3.example/redir"
        )
        resp = client.get(
            f"/api/v1/files/{f.id}/download-direct",
            headers=auth_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 307
        assert resp.headers["location"] == "https://s3.example/redir"


class TestThumbnail:
    def test_legacy_png_fallback(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="legacy-thumb", hash_="8" * 64)
        f = _make_file(db_session, model)
        backend = get_backend()
        # File IDs are reused across tests (DB is truncated, not the storage
        # backend singleton) — clear any blob a prior test left at this key.
        backend.delete(backend.thumbnail_key(f.id))
        backend.delete(backend.legacy_thumbnail_key(f.id))
        backend.write_bytes(b"legacy-png-bytes", backend.legacy_thumbnail_key(f.id))

        resp = client.get(f"/api/v1/files/{f.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == b"legacy-png-bytes"

    def test_no_thumbnail_404(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="no-thumb-2", hash_="9" * 64)
        f = _make_file(db_session, model)
        backend = get_backend()
        backend.delete(backend.thumbnail_key(f.id))
        backend.delete(backend.legacy_thumbnail_key(f.id))

        resp = client.get(f"/api/v1/files/{f.id}/thumbnail", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "thumbnail_not_found"


class TestFileAsStl:
    def test_missing_blob_410(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="stl-missing", hash_="a1" * 32)
        f = _make_file(db_session, model)  # path points nowhere
        resp = client.get(f"/api/v1/files/{f.id}/stl", headers=auth_headers)
        assert resp.status_code == 410
        assert resp.json()["detail"] == "file_blob_missing"

    def test_already_stl_served_directly(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="stl-direct", hash_="a2" * 32)
        key = "already.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        f = _make_file(db_session, model, filename="already.stl", path=key)
        resp = client.get(f"/api/v1/files/{f.id}/stl", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == b"solid x endsolid"

    def test_etag_revalidation_returns_304(
        self, client: TestClient, db_session: Session, auth_headers
    ) -> None:
        model = _make_model(db_session, slug="stl-etag", hash_="a3" * 32)
        key = "etag.stl"
        get_backend().write_bytes(b"solid y endsolid", key)
        f = _make_file(db_session, model, filename="etag.stl", path=key)
        resp = client.get(
            f"/api/v1/files/{f.id}/stl",
            headers={**auth_headers, "if-none-match": f'"{f.sha256}"'},
        )
        assert resp.status_code == 304

    def test_3mf_conversion_cached_on_second_request(
        self, client: TestClient, db_session: Session, auth_headers, monkeypatch
    ) -> None:
        model = _make_model(db_session, slug="stl-3mf", hash_="a4" * 32)
        key = "model.3mf"
        get_backend().write_bytes(b"fake-3mf-bytes", key)
        sha = "c1" * 32
        f = _make_file(
            db_session, model, filename="model.3mf", ftype="3mf", path=key,
            sha256=sha,
        )
        backend = get_backend()
        # The STL conversion cache is content-addressed by sha256 and lives on
        # the storage backend's disk, which isn't reset between test runs (only
        # the DB is truncated) — clear any stale cache entry from a prior run.
        backend.delete(backend.stl_cache_key(sha))

        calls = {"n": 0}

        def fake_to_stl_bytes(_path):
            calls["n"] += 1
            return b"converted-stl-bytes"

        monkeypatch.setattr(
            "app.services.mesh_processing.to_stl_bytes", fake_to_stl_bytes
        )

        first = client.get(f"/api/v1/files/{f.id}/stl", headers=auth_headers)
        assert first.status_code == 200
        assert first.content == b"converted-stl-bytes"
        assert calls["n"] == 1

        second = client.get(f"/api/v1/files/{f.id}/stl", headers=auth_headers)
        assert second.status_code == 200
        assert second.content == b"converted-stl-bytes"
        # Cached on the second call — conversion doesn't run again.
        assert calls["n"] == 1

    def test_conversion_failure_500(
        self, client: TestClient, db_session: Session, auth_headers, monkeypatch
    ) -> None:
        model = _make_model(db_session, slug="stl-fail", hash_="a5" * 32)
        key = "broken.obj"
        get_backend().write_bytes(b"not-really-obj", key)
        f = _make_file(
            db_session, model, filename="broken.obj", ftype="obj", path=key,
            sha256="c2" * 32,
        )

        monkeypatch.setattr(
            "app.services.mesh_processing.to_stl_bytes", lambda _path: None
        )

        resp = client.get(f"/api/v1/files/{f.id}/stl", headers=auth_headers)
        assert resp.status_code == 500
        assert resp.json()["detail"] == "stl_conversion_failed"


class TestThumbnailRebuildInternals:
    """Exercises _run_thumbnail_rebuild branches directly — skip-no-mesh,
    skip-missing-blob, failed-render, and the top-level exception guard —
    which the happy-path API test in test_ingest_api.py doesn't reach."""

    def test_skips_model_without_mesh_file(self, db_session: Session) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = _make_model(db_session, slug="no-mesh", hash_="b1" * 32)
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert model.id in status.result["skipped_no_mesh"]

    def test_records_model_with_missing_blob_as_failed_render(
        self, db_session: Session
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = _make_model(db_session, slug="blob-missing", hash_="b2" * 32)
        _make_file(db_session, model, filename="gone.stl", path="/nowhere/gone.stl")
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert model.id in status.result["failed_render"]

    def test_failed_render_recorded(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = _make_model(db_session, slug="render-fails", hash_="b3" * 32)
        key = "render-fails.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        _make_file(db_session, model, filename="render-fails.stl", path=key)

        monkeypatch.setattr(
            "app.services.mesh_processing.render_thumbnail", lambda _path: None
        )

        job_id = registry.create(owner_user_id=None)
        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert model.id in status.result["failed_render"]

    def test_render_exception_recorded_as_failed(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = _make_model(db_session, slug="render-crashes", hash_="b4" * 32)
        key = "render-crashes.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        _make_file(db_session, model, filename="render-crashes.stl", path=key)

        def boom(_path):
            raise RuntimeError("renderer exploded")

        monkeypatch.setattr(
            "app.services.mesh_processing.render_thumbnail", boom
        )

        job_id = registry.create(owner_user_id=None)
        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert model.id in status.result["failed_render"]

    def test_top_level_exception_marks_job_failed(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        def boom(*_a, **_kw):
            raise RuntimeError("db exploded")

        monkeypatch.setattr("app.api.v1.files.select", boom)

        job_id = registry.create(owner_user_id=None)
        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert "db exploded" in (status.error or "")

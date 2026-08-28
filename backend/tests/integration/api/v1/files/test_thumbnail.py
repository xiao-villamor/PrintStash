"""Serving a file's thumbnail, and rebuilding the ones that are missing.

A thumbnail is fetched on every card in the grid, so its caching is a feature rather
than a detail: the response carries an ETag, a matching `If-None-Match` gets a bodiless
304, and a *regenerated* thumbnail must change the ETag or every browser keeps showing
the old picture. On a remote backend the store's own ETag is used and no bytes are read
at all when the client already has them.

The rebuild job is best-effort by design: a model with no mesh is skipped, a model whose
blob is gone or whose render fails is recorded as failed, and neither stops the pass.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.services.jobs import registry
from app.services.storage_backend import get_backend


class TestFileThumbnail:
    def test_serves_the_thumbnail(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        row = make_file(make_model("thumb-serve"))
        backend = get_backend()
        backend.write_bytes(b"first-thumbnail", backend.thumbnail_key(row.id))

        response = client.get(f"/api/v1/files/{row.id}/thumbnail", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == b"first-thumbnail"

    def test_answers_a_matching_etag_without_a_body(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        row = make_file(make_model("thumb-etag"))
        backend = get_backend()
        backend.write_bytes(b"first-thumbnail", backend.thumbnail_key(row.id))
        etag = client.get(
            f"/api/v1/files/{row.id}/thumbnail", headers=auth_headers
        ).headers["etag"]

        cached = client.get(
            f"/api/v1/files/{row.id}/thumbnail",
            headers={**auth_headers, "if-none-match": etag},
        )

        assert cached.status_code == 304
        assert cached.content == b""

    def test_changes_the_etag_when_the_thumbnail_is_rebuilt(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        row = make_file(make_model("thumb-rebuilt"))
        backend = get_backend()
        key = backend.thumbnail_key(row.id)
        backend.write_bytes(b"first-thumbnail", key)
        etag = client.get(
            f"/api/v1/files/{row.id}/thumbnail", headers=auth_headers
        ).headers["etag"]

        direct = backend.direct_path(key)
        assert direct is not None
        direct.write_bytes(b"second-thumbnail-is-different")
        rebuilt = client.get(
            f"/api/v1/files/{row.id}/thumbnail",
            headers={**auth_headers, "if-none-match": etag},
        )

        # A stale ETag here means every browser keeps the old picture forever.
        assert rebuilt.status_code == 200
        assert rebuilt.headers["etag"] != etag

    def test_uses_the_stores_own_etag_on_a_remote_backend(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        from app.api.v1 import files as files_api
        from app.services.storage_backend import StorageObjectInfo

        row = make_file(make_model("remote-thumb"))

        class RemoteThumbnailBackend:
            streamed = 0

            def thumbnail_key(self, file_id: int) -> str:
                return f"thumbs/{file_id}.webp"

            def legacy_thumbnail_key(self, file_id: int) -> str:
                return f"thumbs/{file_id}.png"

            def object_info(self, key: str) -> StorageObjectInfo | None:
                return StorageObjectInfo(size=6, etag='"remote-thumb-v1"')

            def direct_path(self, key: str):
                return None

            def stream_chunks(self, key: str):
                self.streamed += 1
                yield b"remote"

        remote = RemoteThumbnailBackend()
        monkeypatch.setattr(files_api, "get_backend", lambda: remote)

        response = client.get(
            f"/api/v1/files/{row.id}/thumbnail",
            headers={**auth_headers, "if-none-match": '"remote-thumb-v1"'},
        )

        assert response.status_code == 304
        assert response.headers["etag"] == '"remote-thumb-v1"'
        assert remote.streamed == 0, "a 304 must not read the object"

    def test_falls_back_to_a_legacy_png(
        self, client: TestClient, auth_headers, make_model, make_file, remove_blob
    ) -> None:
        row = make_file(make_model("legacy-thumb"))
        backend = get_backend()
        remove_blob(backend.thumbnail_key(row.id))
        remove_blob(backend.legacy_thumbnail_key(row.id))
        backend.write_bytes(b"legacy-png-bytes", backend.legacy_thumbnail_key(row.id))

        response = client.get(f"/api/v1/files/{row.id}/thumbnail", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == b"legacy-png-bytes"

    def test_reports_a_file_with_no_thumbnail(
        self, client: TestClient, auth_headers, make_model, make_file, remove_blob
    ) -> None:
        row = make_file(make_model("no-thumb-2"))
        backend = get_backend()
        remove_blob(backend.thumbnail_key(row.id))
        remove_blob(backend.legacy_thumbnail_key(row.id))

        response = client.get(f"/api/v1/files/{row.id}/thumbnail", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "thumbnail_not_found"


class TestRegenerateThumbnails:
    def test_queues_a_rebuild_job(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post("/api/v1/files/thumbnails/rebuild", headers=auth_headers)

        assert response.status_code == 202, response.text
        assert response.json()["job_id"]

    def test_rejects_a_non_superuser(self, client: TestClient, user_headers) -> None:
        response = client.post(
            "/api/v1/files/thumbnails/rebuild", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text


class TestThumbnailRebuildJob:
    """`_run_thumbnail_rebuild` — the worker the rebuild endpoint schedules."""

    def test_regenerates_a_models_thumbnail(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        import app.services.thumbnail_repair as repair
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = make_model("render-works")
        key = "render-works.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        make_file(model, filename="render-works.stl", path=key)
        # Rendering a real mesh is `thumbnail_repair`'s job and is covered there;
        # here the question is only what the pass does with a success.
        monkeypatch.setattr(
            repair, "regenerate_model_thumbnail", lambda _session, _model_id: True
        )
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert model.id in status.result["rebuilt"]

    def test_skips_a_model_that_already_has_one_unless_forced(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = make_model("already-has-one")
        key = "already-has-one.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        mesh = make_file(model, filename="already-has-one.stl", path=key)
        model.thumbnail_file_id = mesh.id
        db_session.add(model)
        db_session.commit()
        monkeypatch.setattr(
            "app.services.mesh_processing.render_thumbnail", lambda _path: b"webp"
        )
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, False, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert model.id not in status.result["rebuilt"]

    def test_skips_a_model_without_a_mesh(
        self, db_session: Session, make_model
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = make_model("no-mesh")
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert model.id in status.result["skipped_no_mesh"]

    def test_records_a_model_whose_blob_is_gone_as_failed(
        self, db_session: Session, make_model, make_file
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = make_model("blob-missing-rebuild")
        make_file(model, filename="gone.stl", path="/nowhere/gone.stl")
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert model.id in status.result["failed_render"]

    def test_records_a_render_that_produces_nothing_as_failed(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = make_model("render-fails")
        key = "render-fails.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        make_file(model, filename="render-fails.stl", path=key)
        monkeypatch.setattr(
            "app.services.mesh_processing.render_thumbnail", lambda _path: None
        )
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert model.id in status.result["failed_render"]

    def test_records_a_render_that_raises_as_failed(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        model = make_model("render-crashes")
        key = "render-crashes.stl"
        get_backend().write_bytes(b"solid x endsolid", key)
        make_file(model, filename="render-crashes.stl", path=key)

        def exploding(_path):
            raise RuntimeError("renderer exploded")

        monkeypatch.setattr("app.services.mesh_processing.render_thumbnail", exploding)
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        # One bad model must not end the pass.
        assert status.state == "completed"
        assert model.id in status.result["failed_render"]

    def test_marks_the_job_failed_when_the_pass_itself_dies(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1.files import _run_thumbnail_rebuild
        from app.db.session import get_session_factory

        def exploding(*_args: object, **_kwargs: object):
            raise RuntimeError("db exploded")

        monkeypatch.setattr("app.api.v1.files.select", exploding)
        job_id = registry.create(owner_user_id=None)

        _run_thumbnail_rebuild(job_id, True, get_session_factory())

        status = registry.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert "db exploded" in (status.error or "")

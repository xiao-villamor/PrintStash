"""Handing back the bytes of a file, and refusing to when it is not there.

Three answers are load-bearing here and they are deliberately different. A file whose
row is trashed — or whose model is — is **404**: it does not exist as far as the API is
concerned. A file whose row is live but whose blob is gone is **410**: it existed, and
saying so is what turns a silent empty download into a diagnosable fault. And the
download URL is backend-shaped: local storage streams through the API, S3 hands back a
pre-signed URL so the bytes never transit the app.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.services.storage_backend import LocalStorageBackend, get_backend

PRESIGNED = "https://s3.example.test/pre-signed"


class TestDownloadFile:
    def test_serves_the_stored_bytes(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("downloadable")
        key = "downloadable.stl"
        get_backend().write_bytes(b"stl-bytes", key)
        row = make_file(model, path=key)

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == b"stl-bytes"

    def test_hides_a_file_whose_model_is_trashed(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("soon-gone")
        row = make_file(model)
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

    def test_hides_a_trashed_file(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("host-2")
        row = make_file(model)
        row.deleted_at = utcnow()
        db_session.add(row)
        db_session.commit()

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_hides_a_file_whose_model_row_is_gone(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        model = make_model("orphan-host")
        row = make_file(model)
        # A file row outliving its model is corruption, not a 500.
        db_session.delete(db_session.get(type(model), model.id))
        db_session.commit()

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

    def test_reports_a_missing_blob_as_gone(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("blob-missing")
        row = make_file(model)  # path points nowhere

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"

    def test_reports_a_blob_that_vanishes_after_the_existence_check(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        model = make_model("direct-missing")
        row = make_file(model, path="gone-blob.stl")
        backend = get_backend()
        assert isinstance(backend, LocalStorageBackend)
        # Make exists() lie, so the failure lands at the FileResponse layer rather
        # than the earlier gate — the shape of a blob deleted mid-request.
        monkeypatch.setattr(backend, "exists", lambda _key: True)

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"

    def test_streams_from_a_backend_with_no_local_path(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        from app.api.v1 import files as files_api

        model = make_model("remote-backend")
        row = make_file(model, path="remote.stl")

        class RemoteBackend:
            def exists(self, key: str) -> bool:
                return True

            def direct_path(self, key: str):
                return None

            def stream_chunks(self, key: str):
                yield b"remote-"
                yield b"bytes"

        monkeypatch.setattr(files_api, "get_backend", lambda: RemoteBackend())

        response = client.get(f"/api/v1/files/{row.id}/download", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.content == b"remote-bytes"

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model, make_file
    ) -> None:
        row = make_file(make_model("anon-download"))

        assert client.get(f"/api/v1/files/{row.id}/download").status_code == 401


class TestDownloadUrl:
    def test_points_at_the_api_on_local_storage(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        row = make_file(make_model("local-url"))

        body = client.get(
            f"/api/v1/files/{row.id}/download-url", headers=auth_headers
        ).json()

        assert body["backend"] == "local"
        assert body["url"] == f"/api/v1/files/{row.id}/download"

    def test_hands_back_a_presigned_url_on_object_storage(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        row = make_file(make_model("s3-url"))
        monkeypatch.setattr(
            get_backend(),
            "presigned_download_url",
            lambda key, filename: PRESIGNED,
        )

        body = client.get(
            f"/api/v1/files/{row.id}/download-url", headers=auth_headers
        ).json()

        # The bytes never transit the app when the store can serve them itself.
        assert body["backend"] == "s3"
        assert body["url"] == PRESIGNED
        assert "expires_in" in body


class TestDownloadDirect:
    def test_streams_the_file_on_local_storage(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        model = make_model("direct-local")
        key = "direct-local.stl"
        get_backend().write_bytes(b"stl-bytes", key)
        row = make_file(model, path=key)

        response = client.get(
            f"/api/v1/files/{row.id}/download-direct",
            headers=auth_headers,
            follow_redirects=False,
        )

        assert response.status_code == 200, response.text
        assert response.content == b"stl-bytes"

    def test_redirects_to_the_presigned_url_on_object_storage(
        self,
        client: TestClient,
        auth_headers,
        monkeypatch: pytest.MonkeyPatch,
        make_model,
        make_file,
    ) -> None:
        row = make_file(make_model("direct-s3"))
        monkeypatch.setattr(
            get_backend(),
            "presigned_download_url",
            lambda key, filename: PRESIGNED,
        )

        response = client.get(
            f"/api/v1/files/{row.id}/download-direct",
            headers=auth_headers,
            follow_redirects=False,
        )

        assert response.status_code == 307, response.text
        assert response.headers["location"] == PRESIGNED

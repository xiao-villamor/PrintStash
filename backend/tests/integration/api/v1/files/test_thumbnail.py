"""Serving a file's thumbnail.

A thumbnail is fetched on every card in the grid, so its caching is a feature rather
than a detail: the response carries an ETag, a matching `If-None-Match` gets a bodiless
304, and a *regenerated* thumbnail must change the ETag or every browser keeps showing
the old picture. On a remote backend the store's own ETag is used and no bytes are read
at all when the client already has them.

Producing thumbnails is the ``derive.*`` derivative Jobs' work; the routes that
retry one are covered in ``test_derivatives.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.time import utcnow
from app.db.models import CollectionRole
from app.modules.storage.storage_backend.runtime import get_backend


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

    def test_allows_a_reader_of_the_parent_collection(
        self,
        client,
        make_collection,
        make_model,
        make_file,
        make_user,
        headers_for,
        grant_role,
    ):
        collection = make_collection("Shared thumbnails")
        row = make_file(make_model("Visible", collection=collection))
        reader = make_user("thumbnail-reader")
        grant_role(reader, collection, CollectionRole.VIEW)
        backend = get_backend()
        backend.write_bytes(b"visible-thumbnail", backend.thumbnail_key(row.id))

        response = client.get(
            f"/api/v1/files/{row.id}/thumbnail", headers=headers_for(reader)
        )

        assert response.status_code == 200, response.text
        assert response.content == b"visible-thumbnail"

    def test_denies_an_ungranted_thumbnail(
        self, client, make_collection, make_model, make_file, make_user, headers_for
    ):
        row = make_file(
            make_model("Private", collection=make_collection("Private thumbnails"))
        )
        reader = make_user("thumbnail-outsider")
        backend = get_backend()
        backend.write_bytes(b"private-thumbnail", backend.thumbnail_key(row.id))

        response = client.get(
            f"/api/v1/files/{row.id}/thumbnail", headers=headers_for(reader)
        )

        assert response.status_code == 403, response.text
        assert response.content != b"private-thumbnail"

    @pytest.mark.parametrize(
        "owner", ["file", "model"], ids=["trashed-artifact", "trashed-model"]
    )
    def test_hides_trashed_thumbnail_owners(
        self, client, auth_headers, db_session, make_model, make_file, owner
    ):
        model = make_model("Trashed thumbnail")
        row = make_file(model)
        target = {"file": row, "model": model}[owner]
        target.deleted_at = utcnow()
        db_session.add(target)
        db_session.commit()

        response = client.get(f"/api/v1/files/{row.id}/thumbnail", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_not_found"

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
        from app.modules.storage.storage_backend.contracts import StorageObjectInfo

        row = make_file(make_model("remote-thumb"))

        class RemoteThumbnailBackend:
            def browser_download(self, *args, **kwargs):
                return None

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

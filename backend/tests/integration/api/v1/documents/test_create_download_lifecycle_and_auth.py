"""Document creation, download, lifecycle, and authentication HTTP behaviours."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import CollectionRole, Document
from app.services import taxonomy
from tests.integration.api.v1.documents._router_shared import (
    _headers,
    _overlay,
    _user,
    get_backend,
)
from tests.integration.api.v1.documents.test_documents import _PNG, _grant


class TestCreateDocument:
    def test_non_superuser_cannot_create_a_root_document(
        self, db_session: Session, client: TestClient
    ) -> None:
        user = _user(db_session, "root-document-denied")

        response = client.post(
            "/api/v1/documents",
            json={"name": "Root", "body": "private"},
            headers=_headers(user),
        )

        assert response.status_code == 403, response.text
        assert db_session.exec(select(Document)).all() == []

    @pytest.mark.parametrize(
        ("payload", "invalid_field"),
        [
            pytest.param({"name": ""}, "name", id="empty-name"),
            pytest.param({"name": "x" * 256}, "name", id="overlong-name"),
            pytest.param(
                {"name": "large", "body": "x" * 1_000_001},
                "body",
                id="overlong-body",
            ),
        ],
    )
    def test_rejects_markdown_fields_outside_schema_bounds(
        self,
        payload: dict[str, str],
        invalid_field: str,
        db_session: Session,
        client: TestClient,
    ) -> None:
        admin = _user(db_session, f"invalid-document-{invalid_field}", superuser=True)

        response = client.post(
            "/api/v1/documents", json=payload, headers=_headers(admin)
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "request_validation_failed"
        assert db_session.exec(select(Document)).all() == []


class TestBinaryDownload:
    def test_normalizes_filename_and_sets_safe_download_headers(
        self, tmp_path, db_session: Session, client: TestClient
    ) -> None:
        _overlay["data_dir"] = tmp_path / "files"
        _overlay["thumb_dir"] = tmp_path / "thumbs"
        admin = _user(db_session, "safe-download-admin", superuser=True)

        uploaded = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "../../Unsafe Manual.PDF",
                    b"%PDF-1.4 safe",
                    "application/pdf",
                )
            },
            headers=_headers(admin),
        )
        assert uploaded.status_code == 201, uploaded.text

        response = client.get(
            f"/api/v1/documents/{uploaded.json()['id']}/file",
            headers=_headers(admin),
        )

        assert response.status_code == 200, response.text
        assert uploaded.json()["filename"] == "Unsafe_Manual.pdf"
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in response.headers["content-disposition"]
        assert "Unsafe_Manual.pdf" in response.headers["content-disposition"]

    def test_renames_binary_metadata_without_replacing_bytes(
        self, tmp_path, db_session: Session, client: TestClient
    ) -> None:
        _overlay["data_dir"] = tmp_path / "files"
        _overlay["thumb_dir"] = tmp_path / "thumbs"
        admin = _user(db_session, "binary-rename-admin", superuser=True)
        uploaded = client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", b"%PDF-1.4 unchanged", "application/pdf")},
            headers=_headers(admin),
        ).json()

        renamed = client.put(
            f"/api/v1/documents/{uploaded['id']}",
            json={"name": "New display name"},
            headers=_headers(admin),
        )
        downloaded = client.get(
            f"/api/v1/documents/{uploaded['id']}/file", headers=_headers(admin)
        )

        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "New display name"
        assert renamed.json()["filename"] == uploaded["filename"]
        assert downloaded.content == b"%PDF-1.4 unchanged"


class TestDocumentVisibilityAndLifecycle:
    def test_viewer_cannot_update_a_document(
        self, db_session: Session, client: TestClient
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Viewer docs")
        owner = _user(db_session, "viewer-document-owner")
        viewer = _user(db_session, "viewer-document-viewer")
        _grant(db_session, owner, collection.id, CollectionRole.EDIT)
        _grant(db_session, viewer, collection.id, CollectionRole.VIEW)
        created = client.post(
            "/api/v1/documents",
            json={"name": "Read only", "collection_id": collection.id},
            headers=_headers(owner),
        ).json()

        response = client.put(
            f"/api/v1/documents/{created['id']}",
            json={"name": "Forbidden rename"},
            headers=_headers(viewer),
        )

        assert response.status_code == 403, response.text
        document = db_session.get(Document, created["id"])
        assert document is not None
        db_session.refresh(document)
        assert document.name == "Read only"

    def test_trashed_documents_are_hidden_from_live_read(
        self, db_session: Session, client: TestClient
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Hidden docs")
        owner = _user(db_session, "hidden-document-owner")
        _grant(db_session, owner, collection.id, CollectionRole.EDIT)
        created = client.post(
            "/api/v1/documents",
            json={"name": "Hidden", "collection_id": collection.id},
            headers=_headers(owner),
        ).json()

        client.delete(f"/api/v1/documents/{created['id']}", headers=_headers(owner))
        trashed = client.get(
            f"/api/v1/documents/{created['id']}", headers=_headers(owner)
        )

        assert trashed.status_code == 404, trashed.text
        assert trashed.json()["detail"] == "document_not_found"

    def test_permanent_delete_rejects_a_live_document(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "live-permanent-delete-admin", superuser=True)
        created = client.post(
            "/api/v1/documents",
            json={"name": "Still live"},
            headers=_headers(admin),
        ).json()

        response = client.delete(
            f"/api/v1/documents/{created['id']}/permanent",
            headers=_headers(admin),
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "document_not_found"
        assert db_session.get(Document, created["id"]) is not None

    def test_soft_delete_preserves_a_binary_blob(
        self, tmp_path, db_session: Session, client: TestClient
    ) -> None:
        _overlay["data_dir"] = tmp_path / "files"
        admin = _user(db_session, "soft-delete-binary-admin", superuser=True)
        uploaded = client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", b"%PDF retained", "application/pdf")},
            headers=_headers(admin),
        ).json()

        response = client.delete(
            f"/api/v1/documents/{uploaded['id']}", headers=_headers(admin)
        )

        assert response.status_code == 204, response.text
        document = db_session.get(Document, uploaded["id"])
        assert document is not None and document.deleted_at is not None
        assert get_backend().exists(
            get_backend().document_file_key(document.id, document.filename)
        )

    def test_restore_returns_a_trashed_document_to_live_reads(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "restore-document-admin", superuser=True)
        document = Document(
            name="Restore me",
            kind="markdown",
            body="retained",
            deleted_at=utcnow(),
            created_by=admin.id,
            updated_by=admin.id,
        )
        db_session.add(document)
        db_session.commit()
        db_session.refresh(document)

        response = client.post(
            f"/api/v1/documents/{document.id}/restore", headers=_headers(admin)
        )

        assert response.status_code == 200, response.text
        assert response.json()["body"] == "retained"
        db_session.refresh(document)
        assert document.deleted_at is None


class TestDocumentImages:
    def test_uploads_a_raster_image_to_markdown(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "markdown-image-admin", superuser=True)
        document = client.post(
            "/api/v1/documents",
            json={"name": "Image host"},
            headers=_headers(admin),
        ).json()

        response = client.post(
            f"/api/v1/documents/{document['id']}/images",
            files={"file": ("pixel.png", _PNG, "image/png")},
            headers=_headers(admin),
        )

        assert response.status_code == 201, response.text
        assert response.json()["url"].endswith(".png")

    def test_serves_a_markdown_image_with_safe_headers(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "markdown-image-reader", superuser=True)
        document = client.post(
            "/api/v1/documents",
            json={"name": "Image reader"},
            headers=_headers(admin),
        ).json()
        uploaded = client.post(
            f"/api/v1/documents/{document['id']}/images",
            files={"file": ("pixel.png", _PNG, "image/png")},
            headers=_headers(admin),
        ).json()

        response = client.get(uploaded["url"], headers=_headers(admin))

        assert response.status_code == 200, response.text
        assert response.content == _PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("get", "/api/v1/documents", {}, id="list"),
        pytest.param(
            "post",
            "/api/v1/documents",
            {"json": {"name": "unauthenticated"}},
            id="create",
        ),
        pytest.param("get", "/api/v1/documents/trash", {}, id="trash"),
        pytest.param(
            "post",
            "/api/v1/documents/upload",
            {"files": {"file": ("notes.md", b"text", "text/markdown")}},
            id="upload",
        ),
        pytest.param("get", "/api/v1/documents/999", {}, id="read"),
        pytest.param(
            "put",
            "/api/v1/documents/999",
            {"json": {"name": "renamed"}},
            id="update",
        ),
        pytest.param("delete", "/api/v1/documents/999", {}, id="trash-one"),
        pytest.param("post", "/api/v1/documents/999/restore", {}, id="restore"),
        pytest.param("delete", "/api/v1/documents/999/permanent", {}, id="permanent"),
        pytest.param("get", "/api/v1/documents/999/file", {}, id="file"),
        pytest.param(
            "post",
            "/api/v1/documents/999/images",
            {"files": {"file": ("pixel.png", b"png", "image/png")}},
            id="upload-image",
        ),
        pytest.param(
            "get",
            f"/api/v1/documents/999/images/{'0' * 64}.png",
            {},
            id="image",
        ),
    ],
)
def test_requires_authentication_for_every_document_route(
    method: str, path: str, kwargs: dict, client: TestClient
) -> None:
    response = client.request(method, path, **kwargs)

    assert response.status_code == 401, response.text

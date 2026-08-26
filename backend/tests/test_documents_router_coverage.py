"""Document router lifecycle and upload limits are observable through HTTP.

The cases here protect trash visibility, stable not-found responses, destructive
storage preflight, and both binary and markdown upload-size boundaries.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import Document, DocumentKind
from app.services.storage_backend import get_backend
from tests.test_documents import _headers, _user


class TestListDocumentTrash:
    def test_non_admin_without_collection_access_sees_empty_trash(
        self, db_session: Session, client: TestClient
    ) -> None:
        user = _user(db_session, "trash-no-access")

        response = client.get("/api/v1/documents/trash", headers=_headers(user))

        assert response.status_code == 200, response.text
        assert response.json() == []


class TestRestoreTrashedDocument:
    def test_missing_document_returns_stable_not_found(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "missing-restore-admin", superuser=True)

        response = client.post("/api/v1/documents/999/restore", headers=_headers(admin))

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "document_not_found"


class TestPermanentlyDeleteDocument:
    def test_missing_document_returns_stable_not_found(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "missing-delete-admin", superuser=True)

        response = client.delete(
            "/api/v1/documents/999/permanent", headers=_headers(admin)
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "document_not_found"

    def test_unverified_storage_returns_conflict_without_deleting_document(
        self, db_session: Session, client: TestClient
    ) -> None:
        admin = _user(db_session, "unsafe-document-admin", superuser=True)
        document = Document(
            name="Unsafe",
            kind=DocumentKind.PDF,
            filename="missing.pdf",
            deleted_at=utcnow(),
        )
        db_session.add(document)
        db_session.commit()
        db_session.refresh(document)
        assert not get_backend().exists(
            get_backend().document_file_key(document.id, document.filename)
        )

        response = client.delete(
            f"/api/v1/documents/{document.id}/permanent", headers=_headers(admin)
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_ownership_unverified"
        db_session.expire_all()
        assert db_session.get(Document, document.id) is not None


class TestUploadDocument:
    def test_binary_upload_rejects_declared_bytes_over_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: Session,
        client: TestClient,
    ) -> None:
        admin = _user(db_session, "binary-size-admin", superuser=True)
        monkeypatch.setitem(_overlay, "max_upload_mb", 1)

        response = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "manual.pdf",
                    b"x" * (1024 * 1024 + 1),
                    "application/pdf",
                )
            },
            headers=_headers(admin),
        )

        assert response.status_code == 413, response.text

    def test_markdown_upload_rejects_streamed_bytes_over_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db_session: Session,
        client: TestClient,
    ) -> None:
        admin = _user(db_session, "markdown-size-admin", superuser=True)
        monkeypatch.setitem(_overlay, "max_upload_mb", 1)

        response = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "notes.md",
                    b"x" * (1024 * 1024 + 1),
                    "text/markdown",
                )
            },
            headers=_headers(admin),
        )

        assert response.status_code == 413, response.text

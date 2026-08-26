"""Defends trash at the documents API integration boundary.

A regression could expose, corrupt, or permanently remove the wrong document.
"""

from __future__ import annotations

from ._router_shared import (
    Document,
    DocumentKind,
    Session,
    TestClient,
    _headers,
    _user,
    get_backend,
    utcnow,
)


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

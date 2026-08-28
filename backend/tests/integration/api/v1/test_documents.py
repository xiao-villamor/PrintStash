"""Documents: the notes, manuals and images that live beside a model.

A document is either editable markdown kept in the database or an uploaded blob kept in
storage, and the two rules that matter cut across both. **Nothing is overwritten**: a
blob whose destination key is already occupied is a 409 and the existing bytes are left
exactly as they were, because that key may hold somebody else's file. And **a failed
write leaves nothing behind**: if the row cannot be committed, the blob it staged is
rolled back rather than orphaned where only the vault audit would ever find it.

Reads and writes are governed by the document's collection role — VIEW can read, EDIT
can change, and only a superuser can purge a trashed document for good.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.v1 import documents as documents_router
from app.core.config import _overlay
from app.db.models import CollectionRole, Document, User
from app.services import taxonomy
from app.services.storage_backend import get_backend
from tests.factories import bearer, build_user, grant_collection_role

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)

PDF_BYTES = b"%PDF-1.4 hi"


@pytest.fixture(autouse=True)
def document_storage(tmp_path: Path) -> Path:
    """Every document write touches storage; keep it inside the test's tmp dir."""
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    return tmp_path


def _grant(
    session: Session, user: User, collection_id: int, role: CollectionRole
) -> None:
    grant_collection_role(session, user, collection_id, role)


MEGABYTE = 1024 * 1024
_OVER_A_MEGABYTE = b"%PDF" + b"x" * (2 * MEGABYTE)


@pytest.fixture
def tiny_upload_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1 MB upload cap, so the size guards can be tripped without 512 MB of bytes."""
    monkeypatch.setitem(_overlay, "max_upload_mb", 1)


@pytest.fixture
def admin(db_session: Session) -> User:
    return build_user(db_session, "doc-admin", superuser=True)


@pytest.fixture
def admin_headers(admin: User) -> dict[str, str]:
    return bearer(admin)


@pytest.fixture
def markdown_doc(client: TestClient, admin_headers: dict[str, str]):
    def build(name: str = "Doc", **body: Any) -> dict:
        payload: dict[str, Any] = {"name": name, "collection_id": None, "body": "x"}
        payload.update(body)
        response = client.post("/api/v1/documents", json=payload, headers=admin_headers)
        assert response.status_code == 201, response.text
        return response.json()

    return build


@pytest.fixture
def uploaded_pdf(client: TestClient, admin_headers: dict[str, str]):
    def build(filename: str = "manual.pdf", content: bytes = PDF_BYTES) -> dict:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": (filename, content, "application/pdf")},
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return build


class TestListDocuments:
    def test_lists_a_collections_documents(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Guides")
        created = client.post(
            "/api/v1/documents",
            json={"name": "Assembly", "collection_id": collection.id, "body": "# Step"},
            headers=admin_headers,
        ).json()

        listed = client.get(
            f"/api/v1/documents?collection={collection.path}", headers=admin_headers
        ).json()

        assert [row["id"] for row in listed] == [created["id"]]

    def test_excludes_nested_collections_when_direct_is_set(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Parent")
        child = taxonomy.resolve_or_create_collection(db_session, "Parent/Child")
        direct = client.post(
            "/api/v1/documents",
            json={"name": "Direct", "collection_id": parent.id, "body": "x"},
            headers=admin_headers,
        ).json()
        client.post(
            "/api/v1/documents",
            json={"name": "Nested", "collection_id": child.id, "body": "x"},
            headers=admin_headers,
        )

        listed = client.get(
            f"/api/v1/documents?collection={parent.path}&direct=true",
            headers=admin_headers,
        ).json()

        assert [row["id"] for row in listed] == [direct["id"]]

    def test_lists_root_documents_when_direct_is_set_without_a_collection(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        root = markdown_doc("Root doc")

        listed = client.get(
            "/api/v1/documents?direct=true", headers=admin_headers
        ).json()

        assert root["id"] in [row["id"] for row in listed]

    def test_filters_by_name(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        markdown_doc("Assembly Guide")
        markdown_doc("Unrelated")

        listed = client.get(
            "/api/v1/documents?q=assembly", headers=admin_headers
        ).json()

        assert [row["name"] for row in listed] == ["Assembly Guide"]

    def test_shows_a_user_only_the_collections_they_can_reach(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        theirs = taxonomy.resolve_or_create_collection(db_session, "Theirs")
        mine = taxonomy.resolve_or_create_collection(db_session, "Mine")
        member = build_user(db_session, "partial-access")
        _grant(db_session, member, mine.id, CollectionRole.VIEW)
        visible = client.post(
            "/api/v1/documents",
            json={"name": "Visible", "collection_id": mine.id, "body": "x"},
            headers=admin_headers,
        ).json()
        client.post(
            "/api/v1/documents",
            json={"name": "Hidden", "collection_id": theirs.id, "body": "x"},
            headers=admin_headers,
        )

        listed = client.get("/api/v1/documents", headers=bearer(member)).json()

        assert [row["id"] for row in listed] == [visible["id"]]

    def test_returns_nothing_to_a_user_with_no_collection_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        outsider = build_user(db_session, "no-access")

        listed = client.get("/api/v1/documents", headers=bearer(outsider)).json()

        assert listed == []

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/documents").status_code == 401


class TestCreateDocument:
    def test_returns_the_created_markdown_document(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Guides")

        response = client.post(
            "/api/v1/documents",
            json={"name": "Assembly", "collection_id": collection.id, "body": "# Step"},
            headers=admin_headers,
        )

        assert response.status_code == 201, response.text
        assert response.json()["kind"] == "markdown"

    def test_reports_an_unknown_collection_as_not_found(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/documents",
            json={"name": "Ghost", "collection_id": 999999, "body": "x"},
            headers=admin_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "collection_not_found"

    def test_requires_edit_on_the_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Locked")
        viewer = build_user(db_session, "doc-viewer")
        _grant(db_session, viewer, collection.id, CollectionRole.VIEW)

        response = client.post(
            "/api/v1/documents",
            json={"name": "Nope", "collection_id": collection.id, "body": ""},
            headers=bearer(viewer),
        )

        assert response.status_code == 403, response.text


class TestUploadDocument:
    def test_returns_the_uploaded_document(
        self, client: TestClient, uploaded_pdf
    ) -> None:
        uploaded = uploaded_pdf()

        assert uploaded["kind"] == "pdf"
        assert uploaded["filename"]

    def test_tells_a_client_its_file_is_too_big_rather_than_its_request(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        tiny_upload_limit,
    ) -> None:
        """The per-file cap is what the user is subject to, so it is what they hear.

        The app-wide body ceiling sits above this cap on purpose. With one number
        for both it fired first and every client saw `request_too_large` — which
        reads as "your request is malformed" for a file that is merely large, and
        offers nothing to act on.
        """
        response = client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "manual.pdf",
                    b"%PDF" + b"x" * (2 * MEGABYTE),
                    "application/pdf",
                )
            },
            headers=admin_headers,
        )

        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "upload_too_large"

    def test_names_the_document_after_the_filename_when_none_is_given(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("notes.md", b"# hi", "text/markdown")},
            headers=admin_headers,
        )

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "notes"

    def test_keeps_a_markdown_upload_editable(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("notes.md", b"# hi", "text/markdown")},
            headers=admin_headers,
        )

        assert response.json()["kind"] == "markdown"

    def test_classifies_an_unknown_extension_as_other(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
            headers=admin_headers,
        )

        assert response.status_code == 201, response.text
        assert response.json()["kind"] == "other"

    @pytest.mark.asyncio
    async def test_rejects_a_part_that_declares_more_than_the_upload_limit(
        self, db_session: Session, admin: User, tiny_upload_limit
    ) -> None:
        # Called directly because these three cover the three *sizes* this endpoint
        # bounds — the declared part size, the decoded markdown body, and the
        # stored blob — and only the first is reachable through a client. The row
        # below proves the HTTP path reaches this guard rather than the app-wide
        # body ceiling swallowing it.
        oversized = UploadFile(
            filename="manual.pdf", file=io.BytesIO(_OVER_A_MEGABYTE), size=2 * MEGABYTE
        )

        with pytest.raises(HTTPException) as raised:
            await documents_router.upload_document(
                file=oversized,
                name=None,
                collection_id=None,
                current_user=admin,
                session=db_session,
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_too_large"

    @pytest.mark.asyncio
    async def test_rejects_markdown_longer_than_the_upload_limit(
        self, db_session: Session, admin: User, tiny_upload_limit
    ) -> None:
        # A markdown document's body is stored in the row rather than as a blob,
        # so its length is bounded after the read rather than before it.
        oversized = UploadFile(
            filename="notes.md", file=io.BytesIO(_OVER_A_MEGABYTE), size=None
        )

        with pytest.raises(HTTPException) as raised:
            await documents_router.upload_document(
                file=oversized,
                name=None,
                collection_id=None,
                current_user=admin,
                session=db_session,
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_too_large"

    @pytest.mark.asyncio
    async def test_rejects_a_blob_that_stored_more_than_the_upload_limit(
        self, db_session: Session, admin: User, tiny_upload_limit
    ) -> None:
        # The binary path streams to storage first, so the authoritative size is
        # the one the receipt reports — a stream that turned out larger than it
        # declared is caught here and rolled back rather than kept.
        oversized = UploadFile(
            filename="manual.pdf", file=io.BytesIO(_OVER_A_MEGABYTE), size=None
        )

        with pytest.raises(HTTPException) as raised:
            await documents_router.upload_document(
                file=oversized,
                name=None,
                collection_id=None,
                current_user=admin,
                session=db_session,
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_too_large"

    def test_refuses_to_overwrite_an_occupied_destination(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        occupied = Path(get_backend().document_file_key(1, "manual.pdf"))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"pre-existing user data")

        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", b"%PDF incoming", "application/pdf")},
            headers=admin_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_destination_exists"

    def test_leaves_the_occupied_bytes_alone(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        occupied = Path(get_backend().document_file_key(1, "manual.pdf"))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"pre-existing user data")

        client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", b"%PDF incoming", "application/pdf")},
            headers=admin_headers,
        )

        assert occupied.read_bytes() == b"pre-existing user data"

    def test_creates_no_row_when_the_destination_is_occupied(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        occupied = Path(get_backend().document_file_key(1, "manual.pdf"))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"pre-existing user data")

        client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", b"%PDF incoming", "application/pdf")},
            headers=admin_headers,
        )

        assert db_session.exec(select(Document)).all() == []

    def test_rolls_back_the_blob_when_the_row_cannot_be_saved(
        self,
        client: TestClient,
        db_session: Session,
        admin_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.v1.documents as documents_api

        def failing_receipt(*_args: object, **_kwargs: object):
            raise RuntimeError("ownership ledger unavailable")

        monkeypatch.setattr(documents_api, "record_creation", failing_receipt)

        with pytest.raises(RuntimeError, match="ownership ledger"):
            client.post(
                "/api/v1/documents/upload",
                files={"file": ("manual.pdf", PDF_BYTES, "application/pdf")},
                headers=admin_headers,
            )

        # An orphaned blob would only ever be found by the vault audit.
        assert db_session.exec(select(Document)).all() == []
        assert not list((Path(_overlay["data_dir"])).rglob("*.pdf"))


class TestGetDocument:
    def test_returns_the_document(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Readable", body="hello")

        response = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        assert response.status_code == 200, response.text
        assert response.json()["body"] == "hello"

    def test_reports_an_unknown_document_as_not_found(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.get("/api/v1/documents/999999", headers=admin_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "document_not_found"

    def test_allows_a_viewer(self, client: TestClient, db_session: Session) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Locked")
        owner = build_user(db_session, "doc-owner")
        _grant(db_session, owner, collection.id, CollectionRole.EDIT)
        viewer = build_user(db_session, "doc-viewer")
        _grant(db_session, viewer, collection.id, CollectionRole.VIEW)
        doc = client.post(
            "/api/v1/documents",
            json={"name": "Secret", "collection_id": collection.id, "body": "x"},
            headers=bearer(owner),
        ).json()

        response = client.get(f"/api/v1/documents/{doc['id']}", headers=bearer(viewer))

        assert response.status_code == 200, response.text


class TestUpdateDocument:
    def test_edits_the_body(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Editable", body="before")

        response = client.put(
            f"/api/v1/documents/{doc['id']}",
            json={"body": "after"},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["body"] == "after"

    def test_renames_without_touching_the_body(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Old Name", body="keep")

        response = client.put(
            f"/api/v1/documents/{doc['id']}",
            json={"name": "New Name"},
            headers=admin_headers,
        )

        assert response.json()["name"] == "New Name"
        assert response.json()["body"] == "keep"

    def test_refuses_a_body_on_a_binary_document(
        self, client: TestClient, admin_headers: dict[str, str], uploaded_pdf
    ) -> None:
        doc = uploaded_pdf()

        response = client.put(
            f"/api/v1/documents/{doc['id']}",
            json={"body": "not markdown"},
            headers=admin_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "not_a_markdown_document"

    def test_requires_edit_on_the_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Locked")
        owner = build_user(db_session, "doc-owner")
        _grant(db_session, owner, collection.id, CollectionRole.EDIT)
        viewer = build_user(db_session, "doc-viewer")
        _grant(db_session, viewer, collection.id, CollectionRole.VIEW)
        doc = client.post(
            "/api/v1/documents",
            json={"name": "Secret", "collection_id": collection.id, "body": "x"},
            headers=bearer(owner),
        ).json()

        response = client.put(
            f"/api/v1/documents/{doc['id']}",
            json={"body": "y"},
            headers=bearer(viewer),
        )

        assert response.status_code == 403, response.text


class TestTrashDocument:
    def test_removes_it_from_the_list(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Temporary")

        assert (
            client.delete(
                f"/api/v1/documents/{doc['id']}", headers=admin_headers
            ).status_code
            == 204
        )
        listed = client.get("/api/v1/documents", headers=admin_headers).json()
        assert doc["id"] not in [row["id"] for row in listed]

    def test_requires_edit_on_the_collection(
        self, client: TestClient, db_session: Session
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Locked")
        owner = build_user(db_session, "doc-owner")
        _grant(db_session, owner, collection.id, CollectionRole.EDIT)
        viewer = build_user(db_session, "doc-viewer")
        _grant(db_session, viewer, collection.id, CollectionRole.VIEW)
        doc = client.post(
            "/api/v1/documents",
            json={"name": "Secret", "collection_id": collection.id, "body": "x"},
            headers=bearer(owner),
        ).json()

        response = client.delete(
            f"/api/v1/documents/{doc['id']}", headers=bearer(viewer)
        )

        assert response.status_code == 403, response.text


class TestListDocumentTrash:
    def test_lists_a_trashed_document(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Trashed")
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        trashed = client.get("/api/v1/documents/trash", headers=admin_headers).json()

        assert [row["id"] for row in trashed] == [doc["id"]]

    def test_shows_a_user_only_their_own_collections(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Shared")
        member = build_user(db_session, "trash-member")
        _grant(db_session, member, collection.id, CollectionRole.EDIT)
        mine = client.post(
            "/api/v1/documents",
            json={"name": "Mine", "collection_id": collection.id, "body": "x"},
            headers=bearer(member),
        ).json()
        theirs = client.post(
            "/api/v1/documents",
            json={"name": "Theirs", "collection_id": None, "body": "x"},
            headers=admin_headers,
        ).json()
        client.delete(f"/api/v1/documents/{mine['id']}", headers=admin_headers)
        client.delete(f"/api/v1/documents/{theirs['id']}", headers=admin_headers)

        trashed = client.get("/api/v1/documents/trash", headers=bearer(member)).json()

        assert [row["id"] for row in trashed] == [mine["id"]]

    def test_returns_nothing_to_a_user_with_no_collection_access(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        doc = client.post(
            "/api/v1/documents",
            json={"name": "Hidden", "collection_id": None, "body": "x"},
            headers=admin_headers,
        ).json()
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)
        outsider = build_user(db_session, "trash-outsider")

        trashed = client.get("/api/v1/documents/trash", headers=bearer(outsider)).json()

        assert trashed == []


class TestRestoreDocument:
    def test_brings_the_document_back(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Restorable")
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        restored = client.post(
            f"/api/v1/documents/{doc['id']}/restore", headers=admin_headers
        )

        assert restored.status_code == 200, restored.text
        assert (
            client.get(
                f"/api/v1/documents/{doc['id']}", headers=admin_headers
            ).status_code
            == 200
        )

    def test_reports_a_document_that_is_not_trashed_as_not_found(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Live")

        response = client.post(
            f"/api/v1/documents/{doc['id']}/restore", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "document_not_found"

    def test_requires_edit_on_the_collection(
        self, client: TestClient, db_session: Session, admin_headers: dict[str, str]
    ) -> None:
        collection = taxonomy.resolve_or_create_collection(db_session, "Locked")
        viewer = build_user(db_session, "restore-viewer")
        _grant(db_session, viewer, collection.id, CollectionRole.VIEW)
        doc = client.post(
            "/api/v1/documents",
            json={"name": "Secret", "collection_id": collection.id, "body": "x"},
            headers=admin_headers,
        ).json()
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        response = client.post(
            f"/api/v1/documents/{doc['id']}/restore", headers=bearer(viewer)
        )

        assert response.status_code == 403, response.text


class TestPermanentlyDeleteDocument:
    def test_removes_the_row(
        self,
        client: TestClient,
        db_session: Session,
        admin_headers: dict[str, str],
        uploaded_pdf,
    ) -> None:
        doc = uploaded_pdf()
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        purged = client.delete(
            f"/api/v1/documents/{doc['id']}/permanent", headers=admin_headers
        )

        assert purged.status_code == 204, purged.text
        db_session.expire_all()
        assert db_session.get(Document, doc["id"]) is None

    def test_removes_the_documents_blob(
        self, client: TestClient, admin_headers: dict[str, str], uploaded_pdf
    ) -> None:
        doc = uploaded_pdf()
        key = get_backend().document_file_key(doc["id"], doc["filename"])
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        client.delete(f"/api/v1/documents/{doc['id']}/permanent", headers=admin_headers)

        assert not get_backend().exists(key)

    def test_removes_an_embedded_image_it_created(
        self,
        client: TestClient,
        db_session: Session,
        admin_headers: dict[str, str],
        uploaded_pdf,
    ) -> None:
        from app.services.storage_ownership import record_creation

        doc = uploaded_pdf()
        backend = get_backend()
        image_name = f"{'a' * 64}.png"
        image_key = backend.document_image_key(doc["id"], image_name)
        receipt = backend.create_bytes(_PNG, image_key)
        record_creation(db_session, receipt, object_kind="document_image")
        row = db_session.get(Document, doc["id"])
        row.body = f"![image](/api/v1/documents/{doc['id']}/images/{image_name})"
        db_session.add(row)
        db_session.commit()
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        client.delete(f"/api/v1/documents/{doc['id']}/permanent", headers=admin_headers)

        assert not backend.exists(image_key)

    def test_reports_a_document_that_is_not_trashed_as_not_found(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Live")

        response = client.delete(
            f"/api/v1/documents/{doc['id']}/permanent", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "document_not_found"

    def test_refuses_when_storage_ownership_is_unverified(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        markdown_doc,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.v1.documents as documents_api
        from app.services.storage_ownership import UnsafeStorageDeleteError

        doc = markdown_doc("Unverifiable")
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        def unverified(*_args: object, **_kwargs: object):
            raise UnsafeStorageDeleteError("no receipt for this key")

        monkeypatch.setattr(documents_api, "hard_delete_document", unverified)

        response = client.delete(
            f"/api/v1/documents/{doc['id']}/permanent", headers=admin_headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_ownership_unverified"

    def test_keeps_the_document_when_it_refuses(
        self,
        client: TestClient,
        db_session: Session,
        admin_headers: dict[str, str],
        markdown_doc,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.v1.documents as documents_api
        from app.services.storage_ownership import UnsafeStorageDeleteError

        doc = markdown_doc("Unverifiable")
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)

        def unverified(*_args: object, **_kwargs: object):
            raise UnsafeStorageDeleteError("no receipt for this key")

        monkeypatch.setattr(documents_api, "hard_delete_document", unverified)
        client.delete(f"/api/v1/documents/{doc['id']}/permanent", headers=admin_headers)

        db_session.expire_all()
        assert db_session.get(Document, doc["id"]) is not None

    def test_rejects_a_non_superuser(
        self,
        client: TestClient,
        db_session: Session,
        admin_headers: dict[str, str],
        markdown_doc,
    ) -> None:
        doc = markdown_doc("Purgeable")
        client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)
        operator = build_user(db_session, "purge-operator")

        response = client.delete(
            f"/api/v1/documents/{doc['id']}/permanent", headers=bearer(operator)
        )

        assert response.status_code == 403, response.text


class TestGetDocumentFile:
    def test_serves_the_stored_blob(
        self, client: TestClient, admin_headers: dict[str, str], uploaded_pdf
    ) -> None:
        doc = uploaded_pdf()

        response = client.get(
            f"/api/v1/documents/{doc['id']}/file", headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.content == PDF_BYTES

    def test_reports_a_markdown_document_as_having_no_file(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("MD only")

        response = client.get(
            f"/api/v1/documents/{doc['id']}/file", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "no_file"

    def test_reports_a_missing_blob(
        self, client: TestClient, admin_headers: dict[str, str], uploaded_pdf
    ) -> None:
        doc = uploaded_pdf()
        key = get_backend().document_file_key(doc["id"], doc["filename"])
        direct = get_backend().direct_path(key)
        assert direct is not None
        direct.unlink()  # loss outside PrintStash; unchecked delete is disabled

        response = client.get(
            f"/api/v1/documents/{doc['id']}/file", headers=admin_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "file_blob_missing"


class TestUploadDocumentImage:
    def test_returns_a_url_that_serves_the_image(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Illustrated")

        uploaded = client.post(
            f"/api/v1/documents/{doc['id']}/images",
            files={"file": ("p.png", _PNG, "image/png")},
            headers=admin_headers,
        )

        assert uploaded.status_code == 201, uploaded.text
        assert client.get(uploaded.json()["url"], headers=admin_headers).content == _PNG

    def test_rejects_an_unsupported_image_type(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Illustrated")

        response = client.post(
            f"/api/v1/documents/{doc['id']}/images",
            files={"file": ("notes.svg", b"<svg/>", "image/svg+xml")},
            headers=admin_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "unsupported_image_type"

    def test_rejects_an_empty_file(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Illustrated")

        response = client.post(
            f"/api/v1/documents/{doc['id']}/images",
            files={"file": ("empty.png", b"", "image/png")},
            headers=admin_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "empty_file"

    def test_rejects_an_image_over_the_per_image_cap(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        # The per-image cap is min(10 MB, max_upload_bytes); at the default 512 MB
        # global limit an 11 MB image trips only this narrower check, so it reaches
        # the handler rather than the request-body middleware.
        doc = markdown_doc("Illustrated")

        response = client.post(
            f"/api/v1/documents/{doc['id']}/images",
            files={"file": ("big.png", _PNG + b"x" * (11 * 1024 * 1024), "image/png")},
            headers=admin_headers,
        )

        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "upload_too_large"

    @pytest.mark.asyncio
    async def test_rejects_an_image_that_declared_no_size(
        self, db_session: Session, admin: User, markdown_doc
    ) -> None:
        # Starlette fills `UploadFile.size` for a multipart part, so over HTTP the
        # declared-size check always fires first. A caller that does not — the
        # extension's own upload path constructs the file itself — is what the
        # second, post-buffer check is there for, and this is the only way to
        # reach it. Called directly for that reason, not for convenience.
        doc = markdown_doc("Illustrated")
        oversized = UploadFile(
            filename="big.png",
            file=io.BytesIO(_PNG + b"x" * (11 * 1024 * 1024)),
            size=None,
        )

        with pytest.raises(HTTPException) as raised:
            await documents_router.upload_document_image(
                document_id=doc["id"],
                file=oversized,
                current_user=admin,
                session=db_session,
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_too_large"

    def test_refuses_to_overwrite_an_occupied_destination(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Collision")
        name = f"{hashlib.sha256(_PNG).hexdigest()}.png"
        occupied = Path(get_backend().document_image_key(doc["id"], name))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"not the uploaded image")

        response = client.post(
            f"/api/v1/documents/{doc['id']}/images",
            files={"file": ("p.png", _PNG, "image/png")},
            headers=admin_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "storage_destination_exists"

    def test_leaves_the_occupied_bytes_alone(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Collision")
        name = f"{hashlib.sha256(_PNG).hexdigest()}.png"
        occupied = Path(get_backend().document_image_key(doc["id"], name))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"not the uploaded image")

        client.post(
            f"/api/v1/documents/{doc['id']}/images",
            files={"file": ("p.png", _PNG, "image/png")},
            headers=admin_headers,
        )

        assert occupied.read_bytes() == b"not the uploaded image"

    def test_rolls_back_the_image_when_the_receipt_cannot_be_recorded(
        self,
        client: TestClient,
        admin_headers: dict[str, str],
        markdown_doc,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.api.v1.documents as documents_api

        doc = markdown_doc("Illustrated")
        name = f"{hashlib.sha256(_PNG).hexdigest()}.png"
        key = get_backend().document_image_key(doc["id"], name)

        def failing_receipt(*_args: object, **_kwargs: object):
            raise RuntimeError("ownership ledger unavailable")

        monkeypatch.setattr(documents_api, "record_creation", failing_receipt)

        with pytest.raises(RuntimeError, match="ownership ledger"):
            client.post(
                f"/api/v1/documents/{doc['id']}/images",
                files={"file": ("p.png", _PNG, "image/png")},
                headers=admin_headers,
            )

        assert not get_backend().exists(key)


class TestGetDocumentImage:
    def test_rejects_a_name_that_is_not_a_content_hash(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Illustrated")

        response = client.get(
            f"/api/v1/documents/{doc['id']}/images/not-a-valid-hash.png",
            headers=admin_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "image_not_found"

    def test_reports_a_missing_image(
        self, client: TestClient, admin_headers: dict[str, str], markdown_doc
    ) -> None:
        doc = markdown_doc("Illustrated")

        response = client.get(
            f"/api/v1/documents/{doc['id']}/images/{'0' * 64}.png",
            headers=admin_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "image_not_found"

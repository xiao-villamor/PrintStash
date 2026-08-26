"""Document items: markdown CRUD, file upload/serve, images, RBAC."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import CollectionPermission, CollectionRole, Document, User
from app.services import taxonomy
from app.services.auth import create_access_token, hash_password

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)


def _user(session: Session, name: str, *, superuser: bool = False) -> User:
    user = User(
        username=name,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=superuser,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    scope = "admin" if user.is_superuser else "write"
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope=scope)}"
    }


def _grant(session: Session, user: User, cid: int, role: CollectionRole) -> None:
    session.add(CollectionPermission(user_id=user.id, collection_id=cid, role=role))
    session.commit()


def test_markdown_doc_crud_and_image(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    col = taxonomy.resolve_or_create_collection(db_session, "Guides")
    editor = _user(db_session, "doc-editor")
    _grant(db_session, editor, col.id, CollectionRole.EDIT)
    h = _headers(editor)

    # Create a markdown doc.
    created = client.post(
        "/api/v1/documents",
        json={"name": "Assembly", "collection_id": col.id, "body": "# Step 1"},
        headers=h,
    )
    assert created.status_code == 201
    doc_id = created.json()["id"]
    assert created.json()["kind"] == "markdown"

    # It shows in the collection's document list.
    listed = client.get(f"/api/v1/documents?collection={col.path}", headers=h).json()
    assert [d["id"] for d in listed] == [doc_id]

    # Upload an embeddable image; the returned URL serves it back.
    up = client.post(
        f"/api/v1/documents/{doc_id}/images",
        files={"file": ("p.png", _PNG, "image/png")},
        headers=h,
    )
    assert up.status_code == 201
    img_url = up.json()["url"]
    assert client.get(img_url, headers=h).content == _PNG

    # Edit the body, then read it back.
    edited = client.put(
        f"/api/v1/documents/{doc_id}",
        json={"body": f"# Step 1\n![p]({img_url})"},
        headers=h,
    )
    assert edited.status_code == 200
    assert "![p]" in client.get(f"/api/v1/documents/{doc_id}", headers=h).json()["body"]

    # Soft-delete removes it from the list.
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=h).status_code == 204
    assert (
        client.get(f"/api/v1/documents?collection={col.path}", headers=h).json() == []
    )


def test_pdf_upload_serves_blob(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    col = taxonomy.resolve_or_create_collection(db_session, "Manuals")
    editor = _user(db_session, "pdf-editor")
    _grant(db_session, editor, col.id, CollectionRole.EDIT)
    h = _headers(editor)

    up = client.post(
        "/api/v1/documents/upload",
        files={"file": ("manual.pdf", b"%PDF-1.4 hi", "application/pdf")},
        data={"collection_id": str(col.id)},
        headers=h,
    )
    assert up.status_code == 201
    assert up.json()["kind"] == "pdf"
    doc_id = up.json()["id"]
    served = client.get(f"/api/v1/documents/{doc_id}/file", headers=h)
    assert served.status_code == 200
    assert served.content == b"%PDF-1.4 hi"


def test_binary_document_upload_never_overwrites_destination_collision(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    editor = _user(db_session, "pdf-collision", superuser=True)
    headers = _headers(editor)
    from app.services.storage_backend import get_backend

    backend = get_backend()
    occupied = Path(backend.document_file_key(1, "manual.pdf"))
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing user data")

    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("manual.pdf", b"%PDF incoming", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "storage_destination_exists"
    assert occupied.read_bytes() == b"pre-existing user data"
    assert db_session.exec(select(Document)).all() == []


def test_document_image_collision_is_reported_without_overwrite(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    editor = _user(db_session, "doc-image-collision", superuser=True)
    headers = _headers(editor)
    created = client.post(
        "/api/v1/documents",
        json={"name": "Collision", "body": ""},
        headers=headers,
    ).json()
    from app.services.storage_backend import get_backend

    name = f"{hashlib.sha256(_PNG).hexdigest()}.png"
    occupied = Path(get_backend().document_image_key(created["id"], name))
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"not the uploaded image")

    response = client.post(
        f"/api/v1/documents/{created['id']}/images",
        files={"file": ("p.png", _PNG, "image/png")},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "storage_destination_exists"
    assert occupied.read_bytes() == b"not the uploaded image"


def test_binary_document_trash_restore_and_permanent_delete_removes_blobs(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    superuser = _user(db_session, "document-trash-admin", superuser=True)
    headers = _headers(superuser)
    uploaded = client.post(
        "/api/v1/documents/upload",
        files={"file": ("manual.pdf", b"%PDF-1.4 hi", "application/pdf")},
        headers=headers,
    ).json()
    document_id = uploaded["id"]

    from app.services.storage_backend import get_backend

    backend = get_backend()
    file_key = backend.document_file_key(document_id, uploaded["filename"])
    image_name = f"{'a' * 64}.png"
    image_key = backend.document_image_key(document_id, image_name)
    from app.services.storage_ownership import record_creation

    # This test models an image created by PrintStash. Legacy/unreceipted
    # lookalikes are covered separately and must be preserved.
    receipt = backend.create_bytes(_PNG, image_key)
    record_creation(db_session, receipt, object_kind="document_image")
    db_session.commit()
    document = db_session.get(Document, document_id)
    document.body = f"![image](/api/v1/documents/{document_id}/images/{image_name})"
    db_session.add(document)
    db_session.commit()

    assert (
        client.delete(f"/api/v1/documents/{document_id}", headers=headers).status_code
        == 204
    )
    assert [
        row["id"]
        for row in client.get("/api/v1/documents/trash", headers=headers).json()
    ] == [document_id]
    assert (
        client.post(
            f"/api/v1/documents/{document_id}/restore", headers=headers
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/documents/{document_id}", headers=headers).status_code
        == 200
    )
    assert (
        client.delete(f"/api/v1/documents/{document_id}", headers=headers).status_code
        == 204
    )

    purged = client.delete(
        f"/api/v1/documents/{document_id}/permanent", headers=headers
    )
    assert purged.status_code == 204
    assert not backend.exists(file_key)
    assert not backend.exists(image_key)
    db_session.expire_all()
    assert db_session.get(Document, document_id) is None


def test_document_rbac(db_session: Session, client: TestClient, tmp_path: Path) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    col = taxonomy.resolve_or_create_collection(db_session, "Locked")
    viewer = _user(db_session, "doc-viewer")
    _grant(db_session, viewer, col.id, CollectionRole.VIEW)
    owner = _user(db_session, "doc-owner")
    _grant(db_session, owner, col.id, CollectionRole.EDIT)

    doc_id = client.post(
        "/api/v1/documents",
        json={"name": "Secret", "collection_id": col.id, "body": "x"},
        headers=_headers(owner),
    ).json()["id"]

    # VIEW can read, can't edit or delete.
    assert (
        client.get(f"/api/v1/documents/{doc_id}", headers=_headers(viewer)).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/documents/{doc_id}", json={"body": "y"}, headers=_headers(viewer)
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/documents/{doc_id}", headers=_headers(viewer)
        ).status_code
        == 403
    )
    # Creating in a collection without EDIT is denied.
    assert (
        client.post(
            "/api/v1/documents",
            json={"name": "Nope", "collection_id": col.id, "body": ""},
            headers=_headers(viewer),
        ).status_code
        == 403
    )


def test_list_documents_direct_only(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    parent = taxonomy.resolve_or_create_collection(db_session, "Parent")
    child = taxonomy.resolve_or_create_collection(db_session, "Parent/Child")
    editor = _user(db_session, "direct-editor", superuser=True)
    h = _headers(editor)

    direct_doc = client.post(
        "/api/v1/documents",
        json={"name": "Direct", "collection_id": parent.id, "body": "x"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/documents",
        json={"name": "Nested", "collection_id": child.id, "body": "x"},
        headers=h,
    )

    listed = client.get(
        f"/api/v1/documents?collection={parent.path}&direct=true", headers=h
    ).json()
    assert [d["id"] for d in listed] == [direct_doc["id"]]


def test_list_documents_direct_root_only(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "root-lister", superuser=True)
    h = _headers(superuser)
    root_doc = client.post(
        "/api/v1/documents",
        json={"name": "Root doc", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    listed = client.get("/api/v1/documents?direct=true", headers=h).json()
    assert root_doc["id"] in [d["id"] for d in listed]


def test_list_documents_filters_by_name_query(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "search-lister", superuser=True)
    h = _headers(superuser)
    client.post(
        "/api/v1/documents",
        json={"name": "Assembly Guide", "collection_id": None, "body": "x"},
        headers=h,
    )
    client.post(
        "/api/v1/documents",
        json={"name": "Unrelated", "collection_id": None, "body": "x"},
        headers=h,
    )

    listed = client.get("/api/v1/documents?q=assembly", headers=h).json()
    assert [d["name"] for d in listed] == ["Assembly Guide"]


def test_list_documents_non_superuser_with_no_access_returns_empty(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    unprivileged = _user(db_session, "no-access")
    listed = client.get("/api/v1/documents", headers=_headers(unprivileged)).json()
    assert listed == []


def test_create_document_unknown_collection_denied(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "creator", superuser=True)
    resp = client.post(
        "/api/v1/documents",
        json={"name": "Ghost", "collection_id": 999999, "body": "x"},
        headers=_headers(superuser),
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "collection_not_found"


def test_get_document_unknown_id_returns_404(
    db_session: Session, client: TestClient
) -> None:
    superuser = _user(db_session, "getter", superuser=True)
    resp = client.get("/api/v1/documents/999999", headers=_headers(superuser))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "document_not_found"


def test_update_document_rejects_body_on_non_markdown(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    superuser = _user(db_session, "pdf-updater", superuser=True)
    h = _headers(superuser)
    up = client.post(
        "/api/v1/documents/upload",
        files={"file": ("manual.pdf", b"%PDF-1.4 hi", "application/pdf")},
        headers=h,
    )
    doc_id = up.json()["id"]

    resp = client.put(
        f"/api/v1/documents/{doc_id}", json={"body": "not markdown"}, headers=h
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not_a_markdown_document"


def test_update_document_renames_without_touching_body(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "renamer", superuser=True)
    h = _headers(superuser)
    created = client.post(
        "/api/v1/documents",
        json={"name": "Old Name", "collection_id": None, "body": "keep"},
        headers=h,
    ).json()

    resp = client.put(
        f"/api/v1/documents/{created['id']}",
        json={"name": "New Name"},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"
    assert resp.json()["body"] == "keep"


def test_get_document_file_no_filename_returns_404(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "no-file", superuser=True)
    h = _headers(superuser)
    doc = client.post(
        "/api/v1/documents",
        json={"name": "MD only", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    resp = client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no_file"


def test_get_document_file_missing_blob_returns_404(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    superuser = _user(db_session, "missing-blob", superuser=True)
    h = _headers(superuser)
    up = client.post(
        "/api/v1/documents/upload",
        files={"file": ("manual.pdf", b"%PDF-1.4 hi", "application/pdf")},
        headers=h,
    )
    doc_id = up.json()["id"]

    from app.services.storage_backend import get_backend

    backend = get_backend()
    key = backend.document_file_key(doc_id, up.json()["filename"])
    direct = backend.direct_path(key)
    assert direct is not None
    direct.unlink()  # Simulate loss outside PrintStash; unchecked delete is disabled.

    resp = client.get(f"/api/v1/documents/{doc_id}/file", headers=h)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "file_blob_missing"


def test_upload_document_uses_filename_stem_when_name_omitted(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "stem-namer", superuser=True)
    h = _headers(superuser)
    up = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.md", b"# hi", "text/markdown")},
        headers=h,
    )
    assert up.status_code == 201
    assert up.json()["name"] == "notes"
    assert up.json()["kind"] == "markdown"


def test_upload_document_other_kind_for_unknown_extension(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["data_dir"] = tmp_path / "files"
    superuser = _user(db_session, "other-kind", superuser=True)
    h = _headers(superuser)
    up = client.post(
        "/api/v1/documents/upload",
        files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
        headers=h,
    )
    assert up.status_code == 201
    assert up.json()["kind"] == "other"


def test_upload_document_image_rejects_unsupported_type(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "img-rejector", superuser=True)
    h = _headers(superuser)
    doc = client.post(
        "/api/v1/documents",
        json={"name": "Doc", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    resp = client.post(
        f"/api/v1/documents/{doc['id']}/images",
        files={"file": ("notes.svg", b"<svg/>", "image/svg+xml")},
        headers=h,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported_image_type"


def test_upload_document_image_rejects_empty_file(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "img-empty", superuser=True)
    h = _headers(superuser)
    doc = client.post(
        "/api/v1/documents",
        json={"name": "Doc", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    resp = client.post(
        f"/api/v1/documents/{doc['id']}/images",
        files={"file": ("empty.png", b"", "image/png")},
        headers=h,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "empty_file"


def test_upload_document_image_rejects_over_cap(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    # The per-image cap is fixed at min(10 MB, max_upload_bytes); with the
    # default 512 MB global limit, an 11 MB image trips only this narrower
    # cap — not the global request-body middleware — so it reaches the
    # in-handler check this test targets.
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "img-cap", superuser=True)
    h = _headers(superuser)
    doc = client.post(
        "/api/v1/documents",
        json={"name": "Doc", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    oversized = _PNG + b"x" * (11 * 1024 * 1024)
    resp = client.post(
        f"/api/v1/documents/{doc['id']}/images",
        files={"file": ("big.png", oversized, "image/png")},
        headers=h,
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "upload_too_large"


def test_get_document_image_rejects_bad_name_pattern(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "img-badname", superuser=True)
    h = _headers(superuser)
    doc = client.post(
        "/api/v1/documents",
        json={"name": "Doc", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    resp = client.get(
        f"/api/v1/documents/{doc['id']}/images/not-a-valid-hash.png", headers=h
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "image_not_found"


def test_get_document_image_missing_blob_returns_404(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    superuser = _user(db_session, "img-missing", superuser=True)
    h = _headers(superuser)
    doc = client.post(
        "/api/v1/documents",
        json={"name": "Doc", "collection_id": None, "body": "x"},
        headers=h,
    ).json()

    fake_name = "0" * 64 + ".png"
    resp = client.get(f"/api/v1/documents/{doc['id']}/images/{fake_name}", headers=h)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "image_not_found"

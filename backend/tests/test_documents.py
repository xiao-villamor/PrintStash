"""Document items: markdown CRUD, file upload/serve, images, RBAC."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import CollectionPermission, CollectionRole, User
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
    return {"Authorization": f"Bearer {create_access_token(user.id, user.username, scope=scope)}"}


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
    assert client.get(f"/api/v1/documents?collection={col.path}", headers=h).json() == []


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
    assert client.get(f"/api/v1/documents/{doc_id}", headers=_headers(viewer)).status_code == 200
    assert (
        client.put(
            f"/api/v1/documents/{doc_id}", json={"body": "y"}, headers=_headers(viewer)
        ).status_code
        == 403
    )
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=_headers(viewer)).status_code == 403
    # Creating in a collection without EDIT is denied.
    assert (
        client.post(
            "/api/v1/documents",
            json={"name": "Nope", "collection_id": col.id, "body": ""},
            headers=_headers(viewer),
        ).status_code
        == 403
    )

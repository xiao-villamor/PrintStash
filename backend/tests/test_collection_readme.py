"""Collection readme + self-hosted image upload/serve (RBAC + validation)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import CollectionPermission, CollectionRole, User
from app.services import taxonomy
from app.services.auth import create_access_token, hash_password

# 1x1 transparent PNG.
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


def test_readme_roundtrip_and_image_lifecycle(
    db_session: Session, client: TestClient, tmp_path: Path
) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    col = taxonomy.resolve_or_create_collection(db_session, "Brackets")
    editor = _user(db_session, "editor")
    _grant(db_session, editor, col.id, CollectionRole.EDIT)
    h = _headers(editor)

    # Set + read back the markdown.
    r = client.put(f"/api/v1/collections/{col.id}/readme", json={"readme": "# Notes"}, headers=h)
    assert r.status_code == 200
    assert client.get(f"/api/v1/collections/{col.id}/readme", headers=h).json()["readme"] == "# Notes"

    # Upload an image; the returned URL serves the bytes back.
    up = client.post(
        f"/api/v1/collections/{col.id}/images",
        files={"file": ("pic.png", _PNG, "image/png")},
        headers=h,
    )
    assert up.status_code == 201
    url = up.json()["url"]
    served = client.get(url, headers=h)
    assert served.status_code == 200
    assert served.content == _PNG

    # Non-image extension is rejected.
    bad = client.post(
        f"/api/v1/collections/{col.id}/images",
        files={"file": ("x.svg", b"<svg/>", "image/svg+xml")},
        headers=h,
    )
    assert bad.status_code == 400


def test_readme_rbac(db_session: Session, client: TestClient, tmp_path: Path) -> None:
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    col = taxonomy.resolve_or_create_collection(db_session, "Private")
    viewer = _user(db_session, "viewer")
    _grant(db_session, viewer, col.id, CollectionRole.VIEW)
    outsider = _user(db_session, "outsider")

    # VIEW can read but not write.
    assert client.get(f"/api/v1/collections/{col.id}/readme", headers=_headers(viewer)).status_code == 200
    assert (
        client.put(
            f"/api/v1/collections/{col.id}/readme", json={"readme": "x"}, headers=_headers(viewer)
        ).status_code
        == 403
    )
    # No grant at all → no read.
    assert client.get(f"/api/v1/collections/{col.id}/readme", headers=_headers(outsider)).status_code == 403
    # Path-traversal-shaped image name is rejected before any disk access.
    assert (
        client.get(
            f"/api/v1/collections/{col.id}/images/..%2f..%2fetc%2fpasswd",
            headers=_headers(viewer),
        ).status_code
        == 404
    )

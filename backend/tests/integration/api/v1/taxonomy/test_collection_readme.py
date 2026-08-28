"""Collection readme + self-hosted image upload/serve (RBAC + validation)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import CollectionRole, User
from app.services import taxonomy
from tests.factories import bearer, build_user, grant_collection_role

# 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)


def _grant(session: Session, user: User, cid: int, role: CollectionRole) -> None:
    grant_collection_role(session, user, cid, role)


def _editable_collection(session: Session, tmp_path: Path):
    """A collection plus the headers of a user who may edit it.

    `EDIT` rather than admin, so these tests keep proving the endpoints are reachable
    on the role the UI actually grants a collaborator.
    """
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    collection = taxonomy.resolve_or_create_collection(session, "Brackets")
    editor = build_user(session, "editor")
    _grant(session, editor, collection.id, CollectionRole.EDIT)
    return collection, bearer(editor)


class TestCollectionReadme:
    def test_a_readme_reads_back_as_it_was_written(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        collection, headers = _editable_collection(db_session, tmp_path)

        put = client.put(
            f"/api/v1/collections/{collection.id}/readme",
            json={"readme": "# Notes"},
            headers=headers,
        )

        assert put.status_code == 200, put.text
        assert (
            client.get(
                f"/api/v1/collections/{collection.id}/readme", headers=headers
            ).json()["readme"]
            == "# Notes"
        )

    def test_an_uploaded_image_is_served_from_the_url_it_returned(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        collection, headers = _editable_collection(db_session, tmp_path)

        uploaded = client.post(
            f"/api/v1/collections/{collection.id}/images",
            files={"file": ("pic.png", _PNG, "image/png")},
            headers=headers,
        )

        assert uploaded.status_code == 201, uploaded.text
        served = client.get(uploaded.json()["url"], headers=headers)
        assert served.status_code == 200
        assert served.content == _PNG

    def test_an_upload_that_is_not_an_image_is_rejected(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        # SVG specifically: it is an image to a browser and a script host to an
        # attacker, so serving one back from our own origin is the risk here.
        collection, headers = _editable_collection(db_session, tmp_path)

        rejected = client.post(
            f"/api/v1/collections/{collection.id}/images",
            files={"file": ("x.svg", b"<svg/>", "image/svg+xml")},
            headers=headers,
        )

        assert rejected.status_code == 400, rejected.text

    def test_readme_rbac(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        _overlay["thumb_dir"] = tmp_path / "thumbs"
        col = taxonomy.resolve_or_create_collection(db_session, "Private")
        viewer = build_user(db_session, "viewer")
        _grant(db_session, viewer, col.id, CollectionRole.VIEW)
        outsider = build_user(db_session, "outsider")

        # VIEW can read but not write.
        assert (
            client.get(
                f"/api/v1/collections/{col.id}/readme", headers=bearer(viewer)
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/v1/collections/{col.id}/readme",
                json={"readme": "x"},
                headers=bearer(viewer),
            ).status_code
            == 403
        )
        # No grant at all → no read.
        assert (
            client.get(
                f"/api/v1/collections/{col.id}/readme", headers=bearer(outsider)
            ).status_code
            == 403
        )
        # Path-traversal-shaped image name is rejected before any disk access.
        assert (
            client.get(
                f"/api/v1/collections/{col.id}/images/..%2f..%2fetc%2fpasswd",
                headers=bearer(viewer),
            ).status_code
            == 404
        )

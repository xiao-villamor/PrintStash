"""Collection readme + self-hosted image upload/serve (RBAC + validation)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.v1 import taxonomy as taxonomy_router
from app.core.config import _overlay
from app.db.models import CollectionRole, User
from app.modules.library import taxonomy
from tests.factories import bearer, build_user, grant_collection_role

# 1x1 transparent PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)


def _grant(session: Session, user: User, cid: int, role: CollectionRole) -> None:
    grant_collection_role(session, user, cid, role)


def _set_thumb_root(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".printstash-storage-root.json").write_text(
        json.dumps(
            {
                "format": 1,
                "installation": str(_overlay.get("storage_identity") or "a" * 64),
                "role": "thumb",
            }
        ),
        encoding="utf-8",
    )
    _overlay["thumb_dir"] = path


def _editable_collection(session: Session, tmp_path: Path):
    """A collection plus the headers of a user who may edit it.

    `EDIT` rather than admin, so these tests keep proving the endpoints are reachable
    on the role the UI actually grants a collaborator.
    """
    _set_thumb_root(tmp_path / "thumbs")
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
        _set_thumb_root(tmp_path / "thumbs")
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


class TestHasReadme:
    """`CollectionRead.has_readme` lets a folder view skip the readme request.

    Most folders have no readme, so the flag decides whether opening one costs a
    round-trip. A stale `False` hides a readme; a stale `True` costs a request.
    """

    @staticmethod
    def _listed(
        client: TestClient, headers: dict[str, str], collection_id: int
    ) -> dict:
        rows = client.get("/api/v1/collections", headers=headers).json()
        return next(row for row in rows if row["id"] == collection_id)

    def test_the_list_flags_a_collection_with_a_readme(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        collection, headers = _editable_collection(db_session, tmp_path)
        client.put(
            f"/api/v1/collections/{collection.id}/readme",
            json={"readme": "# Notes"},
            headers=headers,
        )

        assert self._listed(client, headers, collection.id)["has_readme"] is True

    def test_the_list_leaves_a_collection_without_one_unflagged(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        collection, headers = _editable_collection(db_session, tmp_path)

        assert self._listed(client, headers, collection.id)["has_readme"] is False

    def test_emptying_the_readme_clears_the_flag(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        collection, headers = _editable_collection(db_session, tmp_path)
        url = f"/api/v1/collections/{collection.id}/readme"
        client.put(url, json={"readme": "# Notes"}, headers=headers)

        client.put(url, json={"readme": ""}, headers=headers)

        assert self._listed(client, headers, collection.id)["has_readme"] is False

    def test_a_single_collection_read_carries_the_flag(
        self, db_session: Session, client: TestClient, tmp_path: Path
    ) -> None:
        # Rename answers with one CollectionRead built outside the list query.
        collection, _ = _editable_collection(db_session, tmp_path)
        admin = build_user(db_session, "admin", superuser=True)
        client.put(
            f"/api/v1/collections/{collection.id}/readme",
            json={"readme": "# Notes"},
            headers=bearer(admin),
        )

        renamed = client.patch(
            f"/api/v1/collections/{collection.id}",
            json={"name": "Shelf brackets"},
            headers=bearer(admin),
        )

        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["has_readme"] is True


class TestCollectionImageExecution:
    @pytest.mark.asyncio
    async def test_owns_upload_sessions(
        self, app, db_session, auth_headers, make_collection
    ):
        from threading import get_ident

        from app.db.session import get_session_factory, override_session_factory
        from tests.fakes.thread_sessions import ThreadBoundSessionFactory

        collection = make_collection("Image boundary")
        factory = ThreadBoundSessionFactory(db_session.get_bind())
        previous = get_session_factory()
        override_session_factory(factory)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/collections/{collection.id}/images",
                    headers=auth_headers,
                    files={"file": ("pic.png", _PNG, "image/png")},
                )
        finally:
            override_session_factory(previous)
        assert response.status_code == 201, response.text
        assert factory.opened_count > 0
        assert factory.active_count == 0
        assert get_ident() not in factory.thread_ids

    @pytest.mark.asyncio
    async def test_keeps_health_responsive_during_publication(
        self, app, auth_headers, make_collection, monkeypatch
    ):
        collection = make_collection("Image boundary")
        loop = asyncio.get_running_loop()
        observations = []
        publish = taxonomy_router.prepare_bytes
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:

            def delayed_publication(*args, **kwargs):
                health = asyncio.run_coroutine_threadsafe(
                    client.get("/api/v1/health"), loop
                )
                try:
                    observations.append(health.result(timeout=2).status_code)
                except TimeoutError:
                    health.cancel()
                    observations.append(None)
                return publish(*args, **kwargs)

            monkeypatch.setattr(taxonomy_router, "prepare_bytes", delayed_publication)
            response = await client.post(
                f"/api/v1/collections/{collection.id}/images",
                headers=auth_headers,
                files={"file": ("pic.png", _PNG, "image/png")},
            )
        assert response.status_code == 201, response.text
        assert observations == [200]

"""API-level tests for collection/tag endpoints — error branches and
merge/rename/permission paths not covered by tests/test_taxonomy.py
(service-only) or tests/test_collection_rbac.py (RBAC-focused)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.v1 import taxonomy as taxonomy_router
from app.core.config import _overlay
from app.db.models import CollectionRole
from app.services import taxonomy
from app.services.storage_backend import get_backend
from tests.factories import (
    bearer,
    build_model,
    build_user,
    grant_collection_role,
)


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


class TestListCollections:
    def test_list_empty(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        resp = client.get("/api/v1/collections", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_model_counts(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Functional")
        child = taxonomy.resolve_or_create_collection(db_session, "Functional/Brackets")
        assert parent is not None and child is not None
        build_model(
            db_session, name="m1", slug="m1", hash="a" * 64, collection_id=child.id
        )
        db_session.commit()

        resp = client.get("/api/v1/collections", headers=auth_headers)
        assert resp.status_code == 200
        by_path = {c["path"]: c for c in resp.json()}
        assert by_path["functional/brackets"]["model_count"] == 1
        # subtree total rolls up into the parent
        assert by_path["functional"]["model_count"] == 1

    def test_non_superuser_no_access_returns_empty(
        self, client: TestClient, db_session: Session
    ) -> None:
        taxonomy.resolve_or_create_collection(db_session, "Private")
        user = build_user(db_session, "no-access")
        resp = client.get("/api/v1/collections", headers=bearer(user))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_non_superuser_with_access_sees_counts(
        self, client: TestClient, db_session: Session
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Granted")
        assert col is not None
        build_model(
            db_session, name="gm", slug="gm", hash="f" * 64, collection_id=col.id
        )
        user = build_user(db_session, "granted-viewer")
        grant_collection_role(db_session, user, col, CollectionRole.VIEW)

        resp = client.get("/api/v1/collections", headers=bearer(user))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["model_count"] == 1


class TestCreateCollection:
    def test_create_root_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = build_user(db_session, "writer")
        resp = client.post(
            "/api/v1/collections", json={"name": "Root"}, headers=bearer(user)
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "root_collection_admin_required"

    def test_create_root_slash_only_name_is_rejected(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # "/" contains a slash so it takes the root-with-slash path, but
        # resolve_or_create_collection() finds no real segments -> 400.
        resp = client.post(
            "/api/v1/collections", json={"name": "/"}, headers=auth_headers
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "collection_name_required"

    def test_create_root_with_slash_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = build_user(db_session, "writer")
        resp = client.post(
            "/api/v1/collections",
            json={"name": "Functional/Brackets"},
            headers=bearer(user),
        )
        assert resp.status_code == 403

    def test_create_root_with_slash_as_superuser(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/collections",
            json={"name": "Functional/Brackets"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["path"] == "functional/brackets"

    def test_create_with_missing_parent_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/api/v1/collections",
            json={"name": "Child", "parent_id": 999},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "parent_not_found"

    def test_create_duplicate_path_conflict(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.post("/api/v1/collections", json={"name": "Root"}, headers=auth_headers)
        resp = client.post(
            "/api/v1/collections", json={"name": "Root"}, headers=auth_headers
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "collection_already_exists"

    def test_create_revives_trashed_collection(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        from app.core.time import utcnow

        col = taxonomy.resolve_or_create_collection(db_session, "Archive")
        assert col is not None
        col.deleted_at = utcnow()
        db_session.add(col)
        db_session.commit()

        resp = client.post(
            "/api/v1/collections", json={"name": "Archive"}, headers=auth_headers
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == col.id


class TestMoveCollection:
    def test_move_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.patch(
            "/api/v1/collections/999", json={"name": "X"}, headers=auth_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "collection_not_found"

    def test_rename_blank_name_rejected(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Widgets")
        assert col is not None
        resp = client.patch(
            f"/api/v1/collections/{col.id}", json={"name": "   "}, headers=auth_headers
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "collection_name_required"

    def test_move_missing_new_parent_404(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Widgets")
        assert col is not None
        resp = client.patch(
            f"/api/v1/collections/{col.id}",
            json={"parent_id": 999},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "parent_not_found"

    def test_move_into_own_subtree_rejected(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Parent")
        child = taxonomy.resolve_or_create_collection(db_session, "Parent/Child")
        assert parent is not None and child is not None
        resp = client.patch(
            f"/api/v1/collections/{parent.id}",
            json={"parent_id": child.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "circular_reference"

    def test_move_to_root_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Parent")
        child = taxonomy.resolve_or_create_collection(db_session, "Parent/Child")
        assert parent is not None and child is not None
        user = build_user(db_session, "writer")
        grant_collection_role(db_session, user, child, CollectionRole.ADMIN)
        resp = client.patch(
            f"/api/v1/collections/{child.id}",
            json={"parent_id": None},
            headers=bearer(user),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "root_collection_admin_required"

    def test_move_no_op_returns_current(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Same")
        assert col is not None
        resp = client.patch(
            f"/api/v1/collections/{col.id}",
            json={"name": "Same"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == "same"

    def test_rename_keeping_same_parent_updates_path(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        # parent_id unchanged (not in payload) but name changes: exercises the
        # "new_parent_id is not None" branch without a parent move.
        parent = taxonomy.resolve_or_create_collection(db_session, "Fam")
        child = taxonomy.resolve_or_create_collection(db_session, "Fam/Kid")
        assert parent is not None and child is not None
        resp = client.patch(
            f"/api/v1/collections/{child.id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == "fam/renamed"

    def test_move_target_path_collision(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        taxonomy.resolve_or_create_collection(db_session, "Taken")
        other = taxonomy.resolve_or_create_collection(db_session, "Other")
        assert other is not None
        resp = client.patch(
            f"/api/v1/collections/{other.id}",
            json={"name": "Taken"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "collection_already_exists"

    def test_rename_updates_descendant_paths(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Old")
        child = taxonomy.resolve_or_create_collection(db_session, "Old/Kid")
        assert parent is not None and child is not None
        resp = client.patch(
            f"/api/v1/collections/{parent.id}",
            json={"name": "New"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["path"] == "new"
        db_session.refresh(child)
        assert child.path == "new/kid"


class TestCollectionReadme:
    def test_a_collection_readme_round_trips(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Docs")
        assert col is not None
        put = client.put(
            f"/api/v1/collections/{col.id}/readme",
            json={"readme": "# Hello"},
            headers=auth_headers,
        )
        assert put.status_code == 200
        assert put.json()["readme"] == "# Hello"
        get = client.get(f"/api/v1/collections/{col.id}/readme", headers=auth_headers)
        assert get.status_code == 200
        assert get.json()["readme"] == "# Hello"

    def test_set_empty_readme_stores_null(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Docs2")
        assert col is not None
        resp = client.put(
            f"/api/v1/collections/{col.id}/readme",
            json={"readme": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["readme"] is None


class TestCollectionImages:
    def test_upload_unsupported_type_rejected(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs")
        assert col is not None
        resp = client.post(
            f"/api/v1/collections/{col.id}/images",
            files={"file": ("evil.svg", b"<svg></svg>", "image/svg+xml")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "unsupported_image_type"

    def test_upload_empty_file_rejected(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs2")
        assert col is not None
        resp = client.post(
            f"/api/v1/collections/{col.id}/images",
            files={"file": ("empty.png", b"", "image/png")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "empty_file"

    def test_upload_oversized_by_declared_size_rejected(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Stay under the default max_upload_bytes (so the global body-limit
        # middleware lets it through) but over the 10MB image-specific cap —
        # exercises the endpoint's own declared-size check.
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs6")
        assert col is not None
        resp = client.post(
            f"/api/v1/collections/{col.id}/images",
            files={"file": ("big.png", b"x" * (11 * 1024 * 1024), "image/png")},
            headers=auth_headers,
        )
        assert resp.status_code == 413
        assert resp.json()["detail"] == "upload_too_large"

    @pytest.mark.asyncio
    async def test_upload_oversized_without_a_declared_size_rejected(
        self, db_session: Session
    ) -> None:
        # Starlette fills `UploadFile.size` for a multipart part, so over HTTP the
        # declared-size check above always fires first. The second check, after the
        # bytes are buffered, is what protects a caller that supplies no size — and
        # calling the endpoint directly is the only way to reach it.
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs7")
        assert col is not None
        user = build_user(db_session, "image-uploader", superuser=True)
        oversized = UploadFile(
            filename="big.png",
            file=io.BytesIO(b"x" * (11 * 1024 * 1024)),
            size=None,
        )

        with pytest.raises(HTTPException) as raised:
            await taxonomy_router.upload_collection_image(
                collection_id=col.id,
                file=oversized,
                current_user=user,
                session=db_session,
            )

        assert raised.value.status_code == 413
        assert raised.value.detail == "upload_too_large"

    def test_a_collection_image_round_trips(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        _set_thumb_root(tmp_path / "thumbs")
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs3")
        assert col is not None
        data = b"\x89PNG\r\n\x1a\nfake-png-bytes"
        up = client.post(
            f"/api/v1/collections/{col.id}/images",
            files={"file": ("pic.png", data, "image/png")},
            headers=auth_headers,
        )
        assert up.status_code == 201
        url = up.json()["url"]
        get = client.get(url, headers=auth_headers)
        assert get.status_code == 200
        assert get.content == data

    def test_upload_image_collision_never_overwrites_existing_bytes(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        _set_thumb_root(tmp_path / "thumbs")
        col = taxonomy.resolve_or_create_collection(db_session, "Collision")
        data = b"\x89PNG\r\n\x1a\nincoming"
        name = f"{hashlib.sha256(data).hexdigest()}.png"
        destination = Path(get_backend().collection_image_key(col.id, name))
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"user-owned")

        response = client.post(
            f"/api/v1/collections/{col.id}/images",
            files={"file": ("pic.png", data, "image/png")},
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "storage_destination_exists"
        assert destination.read_bytes() == b"user-owned"

    def test_serve_image_bad_name_404(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs4")
        assert col is not None
        resp = client.get(
            f"/api/v1/collections/{col.id}/images/not-a-valid-hash.png",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_serve_image_missing_blob_404(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Imgs5")
        assert col is not None
        name = "a" * 64 + ".png"
        resp = client.get(
            f"/api/v1/collections/{col.id}/images/{name}", headers=auth_headers
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "image_not_found"


class TestDeleteCollection:
    def test_delete_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/api/v1/collections/999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_with_children_blocked(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "HasKids")
        taxonomy.resolve_or_create_collection(db_session, "HasKids/Kid")
        assert parent is not None
        resp = client.delete(f"/api/v1/collections/{parent.id}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "collection_has_children"

    def test_delete_with_models_blocked(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "HasModels")
        assert col is not None
        build_model(db_session, name="m", slug="m", hash="b" * 64, collection_id=col.id)
        db_session.commit()
        resp = client.delete(f"/api/v1/collections/{col.id}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "collection_has_models"

    def test_a_recursive_delete_trashes_every_descendant(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Tree")
        child = taxonomy.resolve_or_create_collection(db_session, "Tree/Branch")
        assert parent is not None and child is not None
        model = build_model(
            db_session, name="leaf", slug="leaf", hash="c" * 64, collection_id=child.id
        )

        resp = client.delete(
            f"/api/v1/collections/{parent.id}?recursive=true", headers=auth_headers
        )
        assert resp.status_code == 204

        db_session.refresh(child)
        db_session.refresh(model)
        assert child.deleted_at is not None
        assert model.deleted_at is not None
        assert model.collection_id is None

    def test_delete_leaf_no_children_no_models(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Empty")
        assert col is not None
        resp = client.delete(f"/api/v1/collections/{col.id}", headers=auth_headers)
        assert resp.status_code == 204


class TestTags:
    def test_list_tags_empty(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get("/api/v1/tags", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tags_with_model_counts(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        (tag,) = taxonomy.resolve_or_create_tags(db_session, ["pla"])
        model = build_model(db_session, name="m", slug="m", hash="d" * 64)
        from app.db.models import ModelTagLink

        db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        db_session.commit()

        resp = client.get("/api/v1/tags", headers=auth_headers)
        assert resp.status_code == 200
        by_name = {t["name"]: t for t in resp.json()}
        assert by_name["pla"]["model_count"] == 1

    def test_list_tags_non_superuser_no_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Tags are a flat, ungated namespace — no-collection-access only zeroes
        # out the per-tag model counts, it doesn't hide the tags themselves.
        taxonomy.resolve_or_create_tags(db_session, ["hidden"])
        user = build_user(db_session, "no-access-2")
        resp = client.get("/api/v1/tags", headers=bearer(user))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["model_count"] == 0

    def test_list_tags_non_superuser_with_access_sees_counts(
        self, client: TestClient, db_session: Session
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "TaggedCol")
        assert col is not None
        (tag,) = taxonomy.resolve_or_create_tags(db_session, ["scoped"])
        model = build_model(
            db_session, name="tm", slug="tm", hash="a1" * 32, collection_id=col.id
        )
        from app.db.models import ModelTagLink

        db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        user = build_user(db_session, "scoped-viewer")
        grant_collection_role(db_session, user, col, CollectionRole.VIEW)

        resp = client.get("/api/v1/tags", headers=bearer(user))
        assert resp.status_code == 200
        by_name = {t["name"]: t for t in resp.json()}
        assert by_name["scoped"]["model_count"] == 1

    def test_create_tag_duplicate_conflict(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.post("/api/v1/tags", json={"name": "abs"}, headers=auth_headers)
        resp = client.post("/api/v1/tags", json={"name": "abs"}, headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "tag_already_exists"

    def test_delete_tag_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/api/v1/tags/999", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "tag_not_found"

    def test_delete_tag_removes_links(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        (tag,) = taxonomy.resolve_or_create_tags(db_session, ["removable"])
        model = build_model(db_session, name="m2", slug="m2", hash="e" * 64)
        from app.db.models import ModelTagLink

        db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        db_session.commit()

        resp = client.delete(f"/api/v1/tags/{tag.id}", headers=auth_headers)
        assert resp.status_code == 204
        db_session.refresh(tag)
        assert tag.deleted_at is not None


class TestCollectionPermissions:
    def test_list_permissions(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Perms")
        assert col is not None
        user = build_user(db_session, "grantee")
        grant_collection_role(db_session, user, col, CollectionRole.VIEW)
        resp = client.get(
            f"/api/v1/collections/{col.id}/permissions", headers=auth_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["username"] == "grantee"

    def test_upsert_permission_creates_then_updates(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Perms2")
        assert col is not None
        target = build_user(db_session, "target")
        resp = client.put(
            f"/api/v1/collections/{col.id}/permissions/{target.id}",
            json={"role": "view"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "view"

        resp2 = client.put(
            f"/api/v1/collections/{col.id}/permissions/{target.id}",
            json={"role": "edit"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["role"] == "edit"

    def test_upsert_permission_unknown_user_404(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Perms3")
        assert col is not None
        resp = client.put(
            f"/api/v1/collections/{col.id}/permissions/999",
            json={"role": "view"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "user_not_found"

    def test_delete_permission_not_found(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Perms4")
        assert col is not None
        target = build_user(db_session, "no-grant")
        resp = client.delete(
            f"/api/v1/collections/{col.id}/permissions/{target.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "permission_not_found"

    def test_delete_permission_success(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Perms5")
        assert col is not None
        target = build_user(db_session, "revoke-me")
        grant_collection_role(db_session, target, col, CollectionRole.VIEW)
        resp = client.delete(
            f"/api/v1/collections/{col.id}/permissions/{target.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    def test_upload_rolls_back_the_written_image_when_the_commit_fails(
        self, client: TestClient, db_session: Session, auth_headers, monkeypatch
    ) -> None:
        """Bytes with no row pointing at them are bytes nothing will ever delete."""
        import io

        from PIL import Image

        from app.api.v1 import taxonomy as taxonomy_api

        col = taxonomy.resolve_or_create_collection(db_session, "Rollback imgs")
        assert col is not None
        db_session.commit()
        collection_id = col.id
        db_session.close()
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "navy").save(image, format="PNG")
        rolled_back: list[object] = []

        real_commit = Session.commit
        commits = 0

        def failing_commit(session: Session) -> None:
            nonlocal commits
            commits += 1
            if commits == 3:
                raise RuntimeError("ownership ledger unavailable")
            real_commit(session)

        monkeypatch.setattr(taxonomy_api.Session, "commit", failing_commit)
        monkeypatch.setattr(
            get_backend(),
            "rollback_create",
            lambda receipt: rolled_back.append(receipt),
        )

        with pytest.raises(RuntimeError, match="ownership ledger unavailable"):
            client.post(
                f"/api/v1/collections/{collection_id}/images",
                headers=auth_headers,
                files={"file": ("cover.png", image.getvalue(), "image/png")},
            )

        assert len(rolled_back) == 1

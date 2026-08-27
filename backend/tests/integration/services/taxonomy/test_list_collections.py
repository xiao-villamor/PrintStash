"""Defends list collections at the services taxonomy integration boundary.

A regression could return or mutate the wrong collection and tag relationships.
"""

from __future__ import annotations

from ._taxonomy_api_shared import (
    CollectionPermission,
    CollectionRole,
    Model,
    Path,
    Session,
    TestClient,
    _headers,
    _user,
    get_backend,
    hashlib,
    taxonomy,
)


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
        db_session.add(
            Model(name="m1", slug="m1", hash="a" * 64, collection_id=child.id)
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
        user = _user(db_session, "no-access")
        resp = client.get("/api/v1/collections", headers=_headers(user))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_non_superuser_with_access_sees_counts(
        self, client: TestClient, db_session: Session
    ) -> None:
        col = taxonomy.resolve_or_create_collection(db_session, "Granted")
        assert col is not None
        db_session.add(Model(name="gm", slug="gm", hash="f" * 64, collection_id=col.id))
        user = _user(db_session, "granted-viewer")
        db_session.add(
            CollectionPermission(
                user_id=user.id, collection_id=col.id, role=CollectionRole.VIEW
            )
        )
        db_session.commit()

        resp = client.get("/api/v1/collections", headers=_headers(user))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["model_count"] == 1


class TestCreateCollection:
    def test_create_root_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        user = _user(db_session, "writer")
        resp = client.post(
            "/api/v1/collections", json={"name": "Root"}, headers=_headers(user)
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
        user = _user(db_session, "writer")
        resp = client.post(
            "/api/v1/collections",
            json={"name": "Functional/Brackets"},
            headers=_headers(user),
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
        user = _user(db_session, "writer")
        db_session.add(
            CollectionPermission(
                user_id=user.id, collection_id=child.id, role=CollectionRole.ADMIN
            )
        )
        db_session.commit()
        resp = client.patch(
            f"/api/v1/collections/{child.id}",
            json={"parent_id": None},
            headers=_headers(user),
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
    def test_set_and_get_readme(
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

    def test_upload_and_serve_roundtrip(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        tmp_path: Path,
    ) -> None:
        from app.core.config import _overlay

        _overlay["thumb_dir"] = tmp_path / "thumbs"
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
        from app.core.config import _overlay

        _overlay["thumb_dir"] = tmp_path / "thumbs"
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
        db_session.add(Model(name="m", slug="m", hash="b" * 64, collection_id=col.id))
        db_session.commit()
        resp = client.delete(f"/api/v1/collections/{col.id}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"] == "collection_has_models"

    def test_delete_recursive_trashes_descendants_and_models(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        parent = taxonomy.resolve_or_create_collection(db_session, "Tree")
        child = taxonomy.resolve_or_create_collection(db_session, "Tree/Branch")
        assert parent is not None and child is not None
        model = Model(name="leaf", slug="leaf", hash="c" * 64, collection_id=child.id)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)

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

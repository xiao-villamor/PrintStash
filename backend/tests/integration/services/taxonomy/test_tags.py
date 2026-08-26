"""Defends tags at the services taxonomy integration boundary.

A regression could return or mutate the wrong collection and tag relationships.
"""

from __future__ import annotations

from ._taxonomy_api_shared import (
    CollectionPermission,
    CollectionRole,
    Model,
    Session,
    TestClient,
    _headers,
    _user,
    taxonomy,
)


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
        model = Model(name="m", slug="m", hash="d" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
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
        user = _user(db_session, "no-access-2")
        resp = client.get("/api/v1/tags", headers=_headers(user))
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
        model = Model(name="tm", slug="tm", hash="a1" * 32, collection_id=col.id)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        from app.db.models import ModelTagLink

        db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        user = _user(db_session, "scoped-viewer")
        db_session.add(
            CollectionPermission(
                user_id=user.id, collection_id=col.id, role=CollectionRole.VIEW
            )
        )
        db_session.commit()

        resp = client.get("/api/v1/tags", headers=_headers(user))
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
        model = Model(name="m2", slug="m2", hash="e" * 64)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
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
        user = _user(db_session, "grantee")
        db_session.add(
            CollectionPermission(
                user_id=user.id, collection_id=col.id, role=CollectionRole.VIEW
            )
        )
        db_session.commit()
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
        target = _user(db_session, "target")
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
        target = _user(db_session, "no-grant")
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
        target = _user(db_session, "revoke-me")
        db_session.add(
            CollectionPermission(
                user_id=target.id, collection_id=col.id, role=CollectionRole.VIEW
            )
        )
        db_session.commit()
        resp = client.delete(
            f"/api/v1/collections/{col.id}/permissions/{target.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

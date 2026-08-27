"""Collection permission endpoint denial boundaries."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import Collection, CollectionPermission, CollectionRole, Tag, User
from app.services import taxonomy
from app.services.auth import create_access_token, hash_password


def _user(session: Session, username: str) -> User:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


def _viewer_and_target(session: Session):
    collection = taxonomy.resolve_or_create_collection(session, "Private")
    assert collection is not None
    viewer = _user(session, "permission-viewer")
    target = _user(session, "permission-target")
    session.add(
        CollectionPermission(
            collection_id=collection.id,
            user_id=viewer.id,
            role=CollectionRole.VIEW,
        )
    )
    session.commit()
    return collection, viewer, target


class TestCollectionPermissionAuthorization:
    def test_list_collection_permissions_requires_admin_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        collection, viewer, _ = _viewer_and_target(db_session)

        # Act
        response = client.get(
            f"/api/v1/collections/{collection.id}/permissions",
            headers=_headers(viewer),
        )

        # Assert
        assert response.status_code == 403

    def test_upsert_collection_permission_requires_admin_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        collection, viewer, target = _viewer_and_target(db_session)

        # Act
        response = client.put(
            f"/api/v1/collections/{collection.id}/permissions/{target.id}",
            headers=_headers(viewer),
            json={"role": "edit"},
        )

        # Assert
        assert response.status_code == 403
        grant = db_session.exec(
            select(CollectionPermission).where(
                CollectionPermission.collection_id == collection.id,
                CollectionPermission.user_id == target.id,
            )
        ).first()
        assert grant is None

    def test_delete_collection_permission_requires_admin_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        collection, viewer, target = _viewer_and_target(db_session)
        permission = CollectionPermission(
            collection_id=collection.id,
            user_id=target.id,
            role=CollectionRole.EDIT,
        )
        db_session.add(permission)
        db_session.commit()

        # Act
        response = client.delete(
            f"/api/v1/collections/{collection.id}/permissions/{target.id}",
            headers=_headers(viewer),
        )

        # Assert
        assert response.status_code == 403
        grant = db_session.exec(
            select(CollectionPermission).where(
                CollectionPermission.collection_id == collection.id,
                CollectionPermission.user_id == target.id,
            )
        ).first()
        assert grant is not None


class TestTaxonomyAuthenticationAndValidation:
    def test_create_collection_requires_authentication(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Act
        response = client.post("/api/v1/collections", json={"name": "Private"})

        # Assert
        assert response.status_code == 401
        assert db_session.exec(select(Collection)).all() == []

    def test_get_collection_readme_missing_collection(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        response = client.get("/api/v1/collections/999999/readme", headers=auth_headers)

        # Assert
        assert response.status_code == 404
        assert response.json()["detail"] == "collection_not_found"

    def test_set_collection_readme_rejects_oversize(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        collection = taxonomy.resolve_or_create_collection(db_session, "Readme")
        assert collection is not None

        # Act
        response = client.put(
            f"/api/v1/collections/{collection.id}/readme",
            headers=auth_headers,
            json={"readme": "x" * 100_001},
        )

        # Assert
        assert response.status_code == 422
        db_session.refresh(collection)
        assert collection.readme is None

    def test_upload_collection_image_requires_edit_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        collection, viewer, _ = _viewer_and_target(db_session)

        # Act
        response = client.post(
            f"/api/v1/collections/{collection.id}/images",
            headers=_headers(viewer),
            files={"file": ("pixel.png", b"not-read", "image/png")},
        )

        # Assert
        assert response.status_code == 403

    def test_delete_collection_requires_admin_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        collection, viewer, _ = _viewer_and_target(db_session)

        # Act
        response = client.delete(
            f"/api/v1/collections/{collection.id}", headers=_headers(viewer)
        )

        # Assert
        assert response.status_code == 403
        db_session.refresh(collection)
        assert collection.deleted_at is None

    def test_create_tag_persists_slug(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Act
        response = client.post(
            "/api/v1/tags", headers=auth_headers, json={"name": "Print Ready"}
        )

        # Assert
        assert response.status_code == 201
        assert response.json()["slug"] == "print-ready"
        assert db_session.get(Tag, response.json()["id"]) is not None

    def test_create_tag_requires_authentication(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Act
        response = client.post("/api/v1/tags", json={"name": "Private"})

        # Assert
        assert response.status_code == 401
        assert db_session.exec(select(Tag)).all() == []

"""Defends ``test_saved_view_crud_is_scoped_to_owner`` behavior for the ``v1`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import Collection, CollectionPermission, CollectionRole, Model, User
from app.services.auth import create_access_token, hash_password


def _user_headers(
    db: Session, username: str, *, scope: str = "write"
) -> tuple[User, dict[str, str]]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.username, scope=scope)
    return user, {"Authorization": f"Bearer {token}"}


def test_saved_view_crud_is_scoped_to_owner(
    client: TestClient, db_session: Session
) -> None:
    owner, owner_headers = _user_headers(db_session, "view-owner")
    _, other_headers = _user_headers(db_session, "view-other")
    payload = {
        "name": "Workshop favorites",
        "filters": {
            "collection": "functional/brackets",
            "direct": True,
            "tag": ["pla", "tested"],
            "q": "mount",
            "favorites": True,
        },
    }

    created = client.post("/api/v1/saved-views", headers=owner_headers, json=payload)
    assert created.status_code == 201
    view_id = created.json()["id"]
    assert created.json()["filters"]["favorites"] is True

    listed = client.get("/api/v1/saved-views", headers=owner_headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [view_id]
    assert (
        client.get(f"/api/v1/saved-views/{view_id}", headers=other_headers).status_code
        == 404
    )

    updated = client.patch(
        f"/api/v1/saved-views/{view_id}",
        headers=owner_headers,
        json={"name": "Ready to print", "filters": {"favorites": True}},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Ready to print"
    assert updated.json()["filters"]["tag"] == []

    assert (
        client.delete(
            f"/api/v1/saved-views/{view_id}", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/saved-views/{view_id}", headers=owner_headers
        ).status_code
        == 204
    )
    assert client.get("/api/v1/saved-views", headers=owner_headers).json() == []
    assert owner.id is not None


def test_saved_view_names_are_unique_per_user(
    client: TestClient, db_session: Session
) -> None:
    _, headers = _user_headers(db_session, "unique-view-owner")
    payload = {"name": "Favorites", "filters": {"favorites": True}}
    assert (
        client.post("/api/v1/saved-views", headers=headers, json=payload).status_code
        == 201
    )
    duplicate = client.post("/api/v1/saved-views", headers=headers, json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "saved_view_name_exists"


def test_star_unstar_and_favorites_filter(
    client: TestClient, db_session: Session
) -> None:
    user, headers = _user_headers(db_session, "model-starrer")
    collection = Collection(name="Shared", slug="shared", path="shared")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    db_session.add(
        CollectionPermission(
            user_id=user.id, collection_id=collection.id, role=CollectionRole.VIEW
        )
    )
    starred = Model(
        name="Starred", slug="starred", hash="1" * 64, collection_id=collection.id
    )
    plain = Model(
        name="Plain", slug="plain", hash="2" * 64, collection_id=collection.id
    )
    db_session.add(starred)
    db_session.add(plain)
    db_session.commit()
    db_session.refresh(starred)

    response = client.put(f"/api/v1/models/{starred.id}/star", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"model_id": starred.id, "starred": True}
    assert (
        client.put(f"/api/v1/models/{starred.id}/star", headers=headers).status_code
        == 200
    )

    favorites = client.get("/api/v1/models?favorites=true&limit=500", headers=headers)
    assert favorites.status_code == 200
    assert [row["id"] for row in favorites.json()] == [starred.id]
    assert favorites.json()[0]["starred"] is True
    detail = client.get(f"/api/v1/models/{starred.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["starred"] is True

    response = client.delete(f"/api/v1/models/{starred.id}/star", headers=headers)
    assert response.status_code == 200
    assert response.json()["starred"] is False
    assert client.get("/api/v1/models?favorites=true", headers=headers).json() == []


def test_starring_requires_model_read_access(
    client: TestClient, db_session: Session
) -> None:
    _, headers = _user_headers(db_session, "no-model-access")
    collection = Collection(name="Private", slug="private", path="private")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    model = Model(
        name="Private model",
        slug="private-model",
        hash="3" * 64,
        collection_id=collection.id,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    response = client.put(f"/api/v1/models/{model.id}/star", headers=headers)
    assert response.status_code == 403

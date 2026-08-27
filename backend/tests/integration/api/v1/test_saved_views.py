"""Saved-view routes preserve private, durable, URL-restorable model filters."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import SavedView, User
from app.services.auth import create_access_token, hash_password

BASE_FILTERS = {
    "collection": "functional/brackets",
    "direct": True,
    "tag": ["pla", "tested"],
    "q": "mount",
    "printer_id": 1,
    "printer_presence": "any",
    "favorites": True,
    "file_type": ["gcode"],
    "material_type": ["PLA"],
    "slicer_name": ["OrcaSlicer"],
    "printer_model": ["Core One"],
    "revision_status": ["known_good"],
    "printed": True,
    "print_outcome": ["completed"],
    "storage": ["vault"],
    "uploaded_after": "2026-01-01T00:00:00Z",
    "uploaded_before": "2026-02-01T00:00:00Z",
}


def _user_headers(
    db_session: Session, username: str, *, scope: str = "write"
) -> tuple[User, dict[str, str]]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope=scope)
    return user, {"Authorization": f"Bearer {token}"}


def _create(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "Workshop favorites",
    filters: dict | None = None,
):
    return client.post(
        "/api/v1/saved-views",
        headers=headers,
        json={"name": name, "filters": filters if filters is not None else {}},
    )


class TestListSavedViews:
    def test_lists_the_current_users_saved_views(self, client, db_session):
        _, headers = _user_headers(db_session, "list-owner")
        beta = _create(client, headers, name="Beta")
        alpha = _create(client, headers, name="Alpha")

        response = client.get("/api/v1/saved-views", headers=headers)

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [
            alpha.json()["id"],
            beta.json()["id"],
        ]

    def test_returns_an_empty_list_when_the_current_user_has_no_saved_views(
        self, client, db_session
    ):
        _, headers = _user_headers(db_session, "empty-owner")

        response = client.get("/api/v1/saved-views", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == []

    def test_excludes_another_users_saved_views_from_the_list(self, client, db_session):
        _, owner_headers = _user_headers(db_session, "scoped-owner")
        _, other_headers = _user_headers(db_session, "scoped-other")
        owned = _create(client, owner_headers)
        _create(client, other_headers, name="Foreign")

        response = client.get("/api/v1/saved-views", headers=owner_headers)

        assert [row["id"] for row in response.json()] == [owned.json()["id"]]

    def test_rejects_an_unauthenticated_saved_view_list(self, client):
        response = client.get("/api/v1/saved-views")

        assert response.status_code == 401, response.text


class TestCreateSavedView:
    def test_creates_a_saved_view_with_the_canonical_filter_contract(
        self, client, db_session
    ):
        _, headers = _user_headers(db_session, "complete-filter-owner")

        response = _create(client, headers, filters=BASE_FILTERS)

        assert response.status_code == 201, response.text
        assert response.json()["filters"] == BASE_FILTERS

    def test_persists_a_created_saved_view_for_the_current_user(
        self, client, db_session
    ):
        owner, headers = _user_headers(db_session, "persist-owner")

        response = _create(client, headers)

        row = db_session.get(SavedView, response.json()["id"])
        assert row.user_id == owner.id

    def test_accepts_empty_filter_collections_and_omitted_optional_filters(
        self, client, db_session
    ):
        _, headers = _user_headers(db_session, "default-filter-owner")

        response = _create(client, headers)

        assert response.status_code == 201, response.text
        assert response.json()["filters"]["tag"] == []
        assert response.json()["filters"]["favorites"] is False

    def test_rejects_a_duplicate_name_for_the_same_user(self, client, db_session):
        owner, headers = _user_headers(db_session, "duplicate-owner")
        _create(client, headers, name="Favorites")

        response = _create(client, headers, name="Favorites")

        rows = db_session.exec(
            select(SavedView).where(SavedView.user_id == owner.id)
        ).all()
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "saved_view_name_exists"
        assert [row.name for row in rows] == ["Favorites"]

    def test_allows_the_same_saved_view_name_for_different_users(
        self, client, db_session
    ):
        _, first_headers = _user_headers(db_session, "same-name-one")
        _, second_headers = _user_headers(db_session, "same-name-two")
        _create(client, first_headers, name="Favorites")

        response = _create(client, second_headers, name="Favorites")

        assert response.status_code == 201, response.text

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"name": "", "filters": {}}, id="empty-name"),
            pytest.param({"name": "x" * 129, "filters": {}}, id="long-name"),
            pytest.param(
                {"name": "Bad enum", "filters": {"storage": ["remote"]}},
                id="invalid-enum",
            ),
            pytest.param(
                {"name": "Too many", "filters": {"tag": ["x"] * 65}},
                id="oversized-array",
            ),
            pytest.param(
                {"name": "Extra", "filters": {}, "unexpected": True}, id="extra-field"
            ),
        ],
    )
    def test_validates_saved_view_create_boundaries(self, client, db_session, payload):
        owner, headers = _user_headers(
            db_session, f"invalid-create-{len(str(payload))}"
        )

        response = client.post("/api/v1/saved-views", headers=headers, json=payload)

        assert response.status_code == 422, response.text
        assert (
            db_session.exec(
                select(SavedView).where(SavedView.user_id == owner.id)
            ).first()
            is None
        )

    @pytest.mark.parametrize(
        "scope",
        [pytest.param(None, id="missing"), pytest.param("read", id="read-scope")],
    )
    def test_rejects_an_unauthenticated_saved_view_create(
        self, client, db_session, scope
    ):
        headers = {}
        if scope is not None:
            _, headers = _user_headers(db_session, "read-create", scope=scope)

        response = _create(client, headers)

        assert response.status_code == 401, response.text
        assert db_session.exec(select(SavedView)).first() is None


class TestGetSavedView:
    def test_returns_the_current_users_saved_view(self, client, db_session):
        _, headers = _user_headers(db_session, "detail-owner")
        created = _create(client, headers, filters={"favorites": True})

        response = client.get(
            f"/api/v1/saved-views/{created.json()['id']}", headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == created.json()

    def test_hides_another_users_saved_view_by_id(self, client, db_session):
        _, owner_headers = _user_headers(db_session, "detail-foreign-owner")
        _, other_headers = _user_headers(db_session, "detail-foreign-other")
        created = _create(client, owner_headers)

        response = client.get(
            f"/api/v1/saved-views/{created.json()['id']}", headers=other_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "saved_view_not_found"

    def test_returns_not_found_for_a_missing_saved_view(self, client, db_session):
        _, headers = _user_headers(db_session, "detail-missing-owner")

        response = client.get("/api/v1/saved-views/999999", headers=headers)

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_saved_view_detail_read(self, client):
        response = client.get("/api/v1/saved-views/1")

        assert response.status_code == 401, response.text


class TestUpdateSavedView:
    def test_updates_a_saved_view_name(self, client, db_session):
        _, headers = _user_headers(db_session, "rename-owner")
        created = _create(client, headers)

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}",
            headers=headers,
            json={"name": "Ready"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Ready"

    def test_updates_a_saved_views_complete_filter_contract(self, client, db_session):
        _, headers = _user_headers(db_session, "filter-update-owner")
        created = _create(client, headers)

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}",
            headers=headers,
            json={"filters": BASE_FILTERS},
        )

        assert response.status_code == 200, response.text
        assert response.json()["filters"] == BASE_FILTERS

    def test_preserves_omitted_fields_during_a_partial_saved_view_update(
        self, client, db_session
    ):
        _, headers = _user_headers(db_session, "partial-update-owner")
        created = _create(client, headers, filters={"favorites": True})

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}",
            headers=headers,
            json={"name": "Renamed"},
        )

        assert response.json()["filters"]["favorites"] is True

    def test_accepts_an_empty_saved_view_update(self, client, db_session):
        _, headers = _user_headers(db_session, "empty-update-owner")
        created = _create(client, headers, filters={"favorites": True})

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}", headers=headers, json={}
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == created.json()["name"]
        assert response.json()["filters"] == created.json()["filters"]

    def test_rejects_a_duplicate_name_during_update(self, client, db_session):
        owner, headers = _user_headers(db_session, "duplicate-update-owner")
        _create(client, headers, name="First")
        second = _create(client, headers, name="Second")

        response = client.patch(
            f"/api/v1/saved-views/{second.json()['id']}",
            headers=headers,
            json={"name": "First"},
        )

        names = db_session.exec(
            select(SavedView.name)
            .where(SavedView.user_id == owner.id)
            .order_by(SavedView.name)
        ).all()
        assert response.status_code == 409, response.text
        assert names == ["First", "Second"]

    def test_hides_another_users_saved_view_during_update(self, client, db_session):
        _, owner_headers = _user_headers(db_session, "update-foreign-owner")
        _, other_headers = _user_headers(db_session, "update-foreign-other")
        created = _create(client, owner_headers)

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}",
            headers=other_headers,
            json={"name": "Stolen"},
        )

        db_session.expire_all()
        assert response.status_code == 404, response.text
        assert (
            db_session.get(SavedView, created.json()["id"]).name
            == created.json()["name"]
        )

    def test_returns_not_found_when_updating_a_missing_saved_view(
        self, client, db_session
    ):
        _, headers = _user_headers(db_session, "update-missing-owner")

        response = client.patch(
            "/api/v1/saved-views/999999", headers=headers, json={"name": "Missing"}
        )

        assert response.status_code == 404, response.text

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"name": ""}, id="empty-name"),
            pytest.param({"name": "x" * 129}, id="long-name"),
            pytest.param({"filters": {"storage": ["remote"]}}, id="invalid-enum"),
            pytest.param({"unexpected": True}, id="extra-field"),
        ],
    )
    def test_validates_saved_view_update_boundaries(self, client, db_session, payload):
        _, headers = _user_headers(db_session, f"invalid-update-{len(str(payload))}")
        created = _create(client, headers)

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}", headers=headers, json=payload
        )

        db_session.expire_all()
        assert response.status_code == 422, response.text
        assert (
            db_session.get(SavedView, created.json()["id"]).name
            == created.json()["name"]
        )

    def test_rejects_an_unauthenticated_saved_view_update(self, client, db_session):
        _, headers = _user_headers(db_session, "unauth-update-owner")
        created = _create(client, headers)

        response = client.patch(
            f"/api/v1/saved-views/{created.json()['id']}", json={"name": "Changed"}
        )

        db_session.expire_all()
        assert response.status_code == 401, response.text
        assert (
            db_session.get(SavedView, created.json()["id"]).name
            == created.json()["name"]
        )


class TestDeleteSavedView:
    def test_deletes_the_current_users_saved_view(self, client, db_session):
        _, headers = _user_headers(db_session, "delete-owner")
        created = _create(client, headers)

        response = client.delete(
            f"/api/v1/saved-views/{created.json()['id']}", headers=headers
        )

        assert response.status_code == 204, response.text
        assert response.content == b""
        assert db_session.get(SavedView, created.json()["id"]) is None

    def test_hides_another_users_saved_view_during_delete(self, client, db_session):
        _, owner_headers = _user_headers(db_session, "delete-foreign-owner")
        _, other_headers = _user_headers(db_session, "delete-foreign-other")
        created = _create(client, owner_headers)

        response = client.delete(
            f"/api/v1/saved-views/{created.json()['id']}", headers=other_headers
        )

        assert response.status_code == 404, response.text
        assert db_session.get(SavedView, created.json()["id"]) is not None

    def test_returns_not_found_when_deleting_a_missing_saved_view(
        self, client, db_session
    ):
        _, headers = _user_headers(db_session, "delete-missing-owner")

        response = client.delete("/api/v1/saved-views/999999", headers=headers)

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_saved_view_delete(self, client, db_session):
        _, headers = _user_headers(db_session, "unauth-delete-owner")
        created = _create(client, headers)

        response = client.delete(f"/api/v1/saved-views/{created.json()['id']}")

        assert response.status_code == 401, response.text
        assert db_session.get(SavedView, created.json()["id"]) is not None

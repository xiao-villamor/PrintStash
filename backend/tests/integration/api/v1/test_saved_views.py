"""A saved view belongs to exactly one user, and the API never says otherwise.

Saved views are named filter sets in the vault's sidebar. Two properties carry the
weight: another user's view is invisible — a read, update or delete of one answers 404,
not 403, because even its existence is not the caller's business — and a name is unique
*per user*, so two people may both keep a "Favorites". If the ownership predicate ever
drops out of a query here, one self-hoster's saved searches start appearing in another's
sidebar, and this file is what goes red.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserHeaders

MAX_NAME = 128
FILTERS: dict[str, Any] = {
    "collection": "functional/brackets",
    "direct": True,
    "tag": ["pla", "tested"],
    "q": "mount",
    "favorites": True,
}


def _create(client: TestClient, headers: dict[str, str], name: str, **filters: Any):
    return client.post(
        "/api/v1/saved-views",
        headers=headers,
        json={"name": name, "filters": filters or FILTERS},
    )


class TestCreateSavedView:
    def test_returns_the_created_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("creator")

        response = _create(client, headers, "Workshop favorites")

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["id"]
        assert body["name"] == "Workshop favorites"
        assert body["filters"]["tag"] == ["pla", "tested"]
        assert body["filters"]["favorites"] is True

    def test_persists_the_view_for_the_caller(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("persister")
        created = _create(client, headers, "Workshop favorites").json()

        listed = client.get("/api/v1/saved-views", headers=headers).json()

        assert [row["id"] for row in listed] == [created["id"]]

    def test_rejects_a_duplicate_name_for_the_same_user(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("duplicator")
        _create(client, headers, "Favorites")

        duplicate = _create(client, headers, "Favorites")

        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"] == "saved_view_name_exists"
        assert len(client.get("/api/v1/saved-views", headers=headers).json()) == 1

    def test_accepts_a_name_another_user_already_uses(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        first = user_headers("first-owner")
        second = user_headers("second-owner")
        _create(client, first, "Favorites")

        response = _create(client, second, "Favorites")

        assert response.status_code == 201, "names are unique per user, not globally"

    def test_rejects_an_unknown_field(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("typo-sender")

        response = client.post(
            "/api/v1/saved-views",
            headers=headers,
            json={"name": "Typo", "filters": FILTERS, "unexpected": "ignored"},
        )

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("", id="empty"),
            pytest.param("x" * (MAX_NAME + 1), id="over-max"),
        ],
    )
    def test_rejects_a_name_outside_the_length_bounds(
        self, client: TestClient, user_headers: UserHeaders, name: str
    ) -> None:
        headers = user_headers(f"bounds-{len(name)}")

        assert _create(client, headers, name).status_code == 422

    def test_accepts_a_name_at_the_length_limit(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("at-limit")
        name = "x" * MAX_NAME

        response = _create(client, headers, name)

        assert response.status_code == 201, response.text
        assert response.json()["name"] == name

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/saved-views", json={"name": "Anon", "filters": FILTERS}
        )

        assert response.status_code == 401

    def test_rejects_a_read_scope_token(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("reader", scope="read")

        response = _create(client, headers, "Read only")

        # `require_auth` answers an insufficient scope with 401, not 403. That is the
        # shipped contract the frontend reads; asserting it here pins it deliberately.
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "insufficient_scope"


class TestListSavedViews:
    def test_lists_only_the_callers_views(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        mine = user_headers("list-owner")
        theirs = user_headers("list-other")
        created = _create(client, mine, "Mine").json()
        _create(client, theirs, "Theirs")

        listed = client.get("/api/v1/saved-views", headers=mine).json()

        assert [row["id"] for row in listed] == [created["id"]]

    def test_returns_an_empty_list_for_a_user_with_none(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("empty-handed")

        assert client.get("/api/v1/saved-views", headers=headers).json() == []

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/saved-views").status_code == 401


class TestGetSavedView:
    def test_returns_the_callers_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("reader-owner")
        created = _create(client, headers, "Mine").json()

        response = client.get(f"/api/v1/saved-views/{created['id']}", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["filters"]["q"] == "mount"

    def test_hides_another_users_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        owner = user_headers("hidden-owner")
        other = user_headers("hidden-other")
        created = _create(client, owner, "Private").json()

        response = client.get(f"/api/v1/saved-views/{created['id']}", headers=other)

        assert response.status_code == 404, "existence is not disclosed"
        assert response.json()["detail"] == "saved_view_not_found"

    def test_reports_an_unknown_id_as_not_found(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("seeker")

        assert (
            client.get("/api/v1/saved-views/9999", headers=headers).status_code == 404
        )


class TestUpdateSavedView:
    def test_renames_the_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("renamer")
        created = _create(client, headers, "Before").json()

        response = client.patch(
            f"/api/v1/saved-views/{created['id']}",
            headers=headers,
            json={"name": "After"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "After"

    def test_replaces_the_whole_filter_set(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("replacer")
        created = _create(client, headers, "Broad").json()

        response = client.patch(
            f"/api/v1/saved-views/{created['id']}",
            headers=headers,
            json={"filters": {"favorites": True}},
        )

        assert response.status_code == 200, response.text
        assert response.json()["filters"]["tag"] == [], (
            "a PATCH of filters replaces the set, it does not merge into it"
        )

    def test_leaves_the_name_alone_when_only_filters_are_sent(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("filter-only")
        created = _create(client, headers, "Keep me").json()

        response = client.patch(
            f"/api/v1/saved-views/{created['id']}",
            headers=headers,
            json={"filters": {"favorites": True}},
        )

        assert response.json()["name"] == "Keep me"

    def test_rejects_a_rename_onto_another_of_the_callers_views(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("collider")
        _create(client, headers, "Taken")
        created = _create(client, headers, "Free").json()

        response = client.patch(
            f"/api/v1/saved-views/{created['id']}",
            headers=headers,
            json={"name": "Taken"},
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "saved_view_name_exists"

    def test_hides_another_users_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        owner = user_headers("patch-owner")
        other = user_headers("patch-other")
        created = _create(client, owner, "Private").json()

        response = client.patch(
            f"/api/v1/saved-views/{created['id']}",
            headers=other,
            json={"name": "Hijacked"},
        )

        assert response.status_code == 404
        assert (
            client.get(f"/api/v1/saved-views/{created['id']}", headers=owner).json()[
                "name"
            ]
            == "Private"
        )

    def test_rejects_a_read_scope_token(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        writer = user_headers("patch-writer")
        created = _create(client, writer, "Mine").json()
        reader = user_headers("patch-reader", scope="read")

        response = client.patch(
            f"/api/v1/saved-views/{created['id']}", headers=reader, json={"name": "No"}
        )

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "insufficient_scope"


class TestDeleteSavedView:
    def test_deletes_the_callers_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("deleter")
        created = _create(client, headers, "Temporary").json()

        response = client.delete(
            f"/api/v1/saved-views/{created['id']}", headers=headers
        )

        assert response.status_code == 204, response.text
        assert client.get("/api/v1/saved-views", headers=headers).json() == []

    def test_hides_another_users_view(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        owner = user_headers("delete-owner")
        other = user_headers("delete-other")
        created = _create(client, owner, "Private").json()

        response = client.delete(f"/api/v1/saved-views/{created['id']}", headers=other)

        assert response.status_code == 404
        assert len(client.get("/api/v1/saved-views", headers=owner).json()) == 1

    def test_reports_a_second_delete_as_not_found(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        headers = user_headers("twice")
        created = _create(client, headers, "Temporary").json()
        client.delete(f"/api/v1/saved-views/{created['id']}", headers=headers)

        again = client.delete(f"/api/v1/saved-views/{created['id']}", headers=headers)

        assert again.status_code == 404

    def test_rejects_a_read_scope_token(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        writer = user_headers("delete-writer")
        created = _create(client, writer, "Mine").json()
        reader = user_headers("delete-reader", scope="read")

        response = client.delete(f"/api/v1/saved-views/{created['id']}", headers=reader)

        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "insufficient_scope"

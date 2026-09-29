"""The collection tree, served a level, a lookup or a search page at a time.

Loading the whole tree on every page is what took a 9,000-collection library a
minute to open (#295). These endpoints replace it for the sidebar, breadcrumbs
and pickers, so each must stay correct on its own: subtree counts that cover
the whole subtree, labels naming ancestors that were never loaded, pages that
neither repeat nor skip, and a viewer who sees exactly the tree they were
granted and nothing above it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.time import utcnow
from app.db.models import CollectionRole
from tests.factories.protocols import (
    GrantRole,
    HeadersFor,
    MakeCollection,
    MakeModel,
    MakeUser,
    TagCollection,
)

CHILDREN = "/api/v1/collections/children"
LOOKUP = "/api/v1/collections/lookup"
SEARCH = "/api/v1/collections/search"


def _names(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


class TestListCollectionChildren:
    def test_lists_the_top_level_by_name(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        make_collection("Toys")
        parts = make_collection("Parts")
        make_collection("Brackets", parent=parts)

        response = client.get(CHILDREN, headers=auth_headers)

        assert _names(response) == ["Parts", "Toys"]

    def test_lists_the_children_of_a_collection(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        make_collection("Gears", parent=parts)
        make_collection("Brackets", parent=parts)

        response = client.get(
            CHILDREN, params={"parent_id": parts.id}, headers=auth_headers
        )

        assert _names(response) == ["Brackets", "Gears"]

    def test_counts_every_model_in_the_subtree(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
        make_model: MakeModel,
    ) -> None:
        parts = make_collection("Parts")
        brackets = make_collection("Brackets", parent=parts)
        make_model("Direct", collection=parts)
        make_model("Nested", collection=make_collection("Small", parent=brackets))

        response = client.get(CHILDREN, headers=auth_headers)

        assert response.json()["items"][0]["model_count"] == 2, response.text

    def test_leaves_a_trashed_model_out_of_the_count(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
        make_model: MakeModel,
    ) -> None:
        parts = make_collection("Parts")
        make_model("Binned", collection=parts, trashed=True)

        response = client.get(CHILDREN, headers=auth_headers)

        assert response.json()["items"][0]["model_count"] == 0, response.text

    def test_reports_how_many_children_a_collection_has(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        make_collection("Brackets", parent=parts)
        make_collection("Gears", parent=parts)

        response = client.get(CHILDREN, headers=auth_headers)

        assert response.json()["items"][0]["child_count"] == 2, response.text

    def test_labels_a_child_with_its_ancestors_names(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        make_collection("Wall Brackets", parent=parts)

        response = client.get(
            CHILDREN, params={"parent_id": parts.id}, headers=auth_headers
        )

        assert response.json()["items"][0]["display_path"] == "Parts/Wall Brackets"

    def test_lists_each_collections_tags(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
        make_tag,
        tag_collection: TagCollection,
    ) -> None:
        parts = make_collection("Parts")
        tag_collection(parts, make_tag("functional"))

        response = client.get(CHILDREN, headers=auth_headers)

        assert response.json()["items"][0]["tags"] == ["functional"], response.text

    def test_orders_names_regardless_of_case(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        make_collection("bolts")
        make_collection("Anchors")
        make_collection("Clips")

        response = client.get(CHILDREN, headers=auth_headers)

        assert _names(response) == ["Anchors", "bolts", "Clips"]

    def test_pages_without_repeating_or_skipping(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        make_collection("Anchors")
        make_collection("Bolts")
        make_collection("Clips")
        first = client.get(CHILDREN, params={"limit": 2}, headers=auth_headers)

        second = client.get(
            CHILDREN,
            params={"limit": 2, "cursor": first.json()["next_cursor"]},
            headers=auth_headers,
        )

        assert _names(first) + _names(second) == ["Anchors", "Bolts", "Clips"]

    def test_ends_the_last_page_without_a_cursor(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        make_collection("Anchors")
        make_collection("Bolts")

        response = client.get(CHILDREN, params={"limit": 2}, headers=auth_headers)

        assert response.json()["next_cursor"] is None, response.text

    def test_leaves_out_a_trashed_collection(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        make_collection("Kept")
        make_collection("Binned", deleted_at=utcnow())

        response = client.get(CHILDREN, headers=auth_headers)

        assert _names(response) == ["Kept"]

    def test_starts_a_viewers_tree_at_the_collections_granted_to_them(
        self,
        client: TestClient,
        make_collection: MakeCollection,
        make_user: MakeUser,
        grant_role: GrantRole,
        headers_for: HeadersFor,
    ) -> None:
        parts = make_collection("Parts")
        brackets = make_collection("Brackets", parent=parts)
        make_collection("Toys")
        viewer = make_user("bracket-viewer")
        grant_role(viewer, brackets, CollectionRole.VIEW)

        response = client.get(CHILDREN, headers=headers_for(viewer))

        assert _names(response) == ["Brackets"]

    def test_labels_a_granted_collection_without_the_ancestors_above_the_grant(
        self,
        client: TestClient,
        make_collection: MakeCollection,
        make_user: MakeUser,
        grant_role: GrantRole,
        headers_for: HeadersFor,
    ) -> None:
        parts = make_collection("Parts")
        brackets = make_collection("Brackets", parent=parts)
        viewer = make_user("label-viewer")
        grant_role(viewer, brackets, CollectionRole.VIEW)

        response = client.get(CHILDREN, headers=headers_for(viewer))

        assert response.json()["items"][0]["display_path"] == "Brackets"

    def test_hides_a_parent_the_viewer_cannot_see(
        self,
        client: TestClient,
        make_collection: MakeCollection,
        make_user: MakeUser,
        headers_for: HeadersFor,
    ) -> None:
        private = make_collection("Private")
        make_collection("Secrets", parent=private)
        viewer = make_user("outsider")

        response = client.get(
            CHILDREN, params={"parent_id": private.id}, headers=headers_for(viewer)
        )

        assert response.status_code == 404, response.text

    def test_rejects_a_malformed_cursor(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(
            CHILDREN, params={"cursor": "not-a-cursor"}, headers=auth_headers
        )

        assert response.status_code == 400, response.text

    def test_rejects_a_page_larger_than_the_maximum(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(CHILDREN, params={"limit": 501}, headers=auth_headers)

        assert response.status_code == 422, response.text

    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get(CHILDREN)

        assert response.status_code == 401, response.text


class TestLookupCollection:
    def test_returns_the_collection_at_a_path(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        make_collection("Brackets", parent=parts)

        response = client.get(
            LOOKUP, params={"path": "parts/brackets"}, headers=auth_headers
        )

        assert response.json()["collection"]["name"] == "Brackets", response.text

    def test_lists_its_ancestors_from_the_root_down(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        brackets = make_collection("Brackets", parent=parts)
        make_collection("Small", parent=brackets)

        response = client.get(
            LOOKUP, params={"path": "parts/brackets/small"}, headers=auth_headers
        )

        ancestors = [a["name"] for a in response.json()["ancestors"]]
        assert ancestors == ["Parts", "Brackets"], response.text

    def test_answers_an_unknown_path_with_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(LOOKUP, params={"path": "nowhere"}, headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_answers_a_path_the_viewer_cannot_see_with_not_found(
        self,
        client: TestClient,
        make_collection: MakeCollection,
        make_user: MakeUser,
        headers_for: HeadersFor,
    ) -> None:
        make_collection("Private")
        viewer = make_user("lookup-outsider")

        response = client.get(
            LOOKUP, params={"path": "private"}, headers=headers_for(viewer)
        )

        assert response.status_code == 404, response.text

    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get(LOOKUP, params={"path": "parts"})

        assert response.status_code == 401, response.text


class TestSearchCollections:
    def test_finds_names_containing_the_query_in_any_case(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        make_collection("Wall Brackets", parent=parts)
        make_collection("Gears", parent=parts)

        response = client.get(SEARCH, params={"q": "BRACK"}, headers=auth_headers)

        assert _names(response) == ["Wall Brackets"]

    def test_pages_through_everything_for_an_empty_query(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        parts = make_collection("Parts")
        make_collection("Gears", parent=parts)

        response = client.get(SEARCH, headers=auth_headers)

        assert _names(response) == ["Gears", "Parts"]

    def test_offers_only_collections_held_at_the_minimum_role(
        self,
        client: TestClient,
        make_collection: MakeCollection,
        make_user: MakeUser,
        grant_role: GrantRole,
        headers_for: HeadersFor,
    ) -> None:
        readable = make_collection("Readable")
        writable = make_collection("Writable")
        user = make_user("uploader")
        grant_role(user, readable, CollectionRole.VIEW)
        grant_role(user, writable, CollectionRole.EDIT)

        response = client.get(
            SEARCH, params={"min_role": "edit"}, headers=headers_for(user)
        )

        assert _names(response) == ["Writable"]

    def test_matches_a_percent_sign_literally(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
    ) -> None:
        make_collection("100% infill", slug="full-infill")
        make_collection("Anything")

        response = client.get(SEARCH, params={"q": "%"}, headers=auth_headers)

        assert _names(response) == ["100% infill"]

    def test_requires_authentication(self, client: TestClient) -> None:
        response = client.get(SEARCH, params={"q": "parts"})

        assert response.status_code == 401, response.text

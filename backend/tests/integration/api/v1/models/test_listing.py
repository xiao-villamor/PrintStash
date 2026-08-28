"""Reading the library: the grid, the cursor page, the outliner, the facets, the trash.

Five endpoints share one filter object, and the one thing they must agree on is who is
allowed to ask what. Filtering by printer joins against printer state, which an ordinary
user has no business reading, so `printer_id` and `printer_presence` are **superuser-only
on all three** endpoints that accept them — a gate that is easy to add to one and forget on
the next two.

The cursor page is the grid's real pagination. Its cursor encodes the sort and filters it
was issued under, so presenting it back under a different sort is a 400 rather than a page
resumed from a meaningless position. And the outliner is deliberately thin: it feeds a
tree of thousands of leaves, so it returns four fields and nothing else.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import Model
from tests.factories import build_model

OUTLINER_FIELDS = {"id", "name", "collection", "collection_id"}
PRINTER_FILTERS = [
    pytest.param({"printer_id": 1}, id="printer_id"),
    pytest.param({"printer_presence": "any"}, id="printer_presence"),
]


@pytest.fixture
def make_model(db_session: Session):
    made = {"n": 0}

    def build(name: str, **overrides) -> Model:
        made["n"] += 1
        return build_model(
            db_session,
            name=name,
            slug=f"listing-{made['n']}",
            hash=f"{made['n']:064d}",
            **overrides,
        )

    return build


class TestListModels:
    def test_lists_the_library(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Bracket")

        response = client.get("/api/v1/models", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert row.id in {item["id"] for item in response.json()}

    def test_hides_a_trashed_model(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Trashed bracket", deleted_at=utcnow())

        response = client.get("/api/v1/models", headers=auth_headers)

        assert row.id not in {item["id"] for item in response.json()}

    def test_matches_a_name_substring(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        wanted = make_model("Articulated Dragon")
        make_model("Bracket")

        response = client.get("/api/v1/models?q=dragon", headers=auth_headers)

        assert [item["id"] for item in response.json()] == [wanted.id]

    @pytest.mark.parametrize("query", PRINTER_FILTERS)
    def test_refuses_a_printer_filter_from_an_ordinary_user(
        self, client: TestClient, user_headers, query: dict
    ) -> None:
        response = client.get(
            "/api/v1/models", headers=user_headers("list-ordinary"), params=query
        )

        # The filter joins against printer state, which is not theirs to read.
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "admin_required"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models").status_code == 401


class TestPageModels:
    def test_returns_a_page_in_the_requested_order(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("API Page Zulu")
        make_model("API Page Alpha")

        response = client.get(
            "/api/v1/models/page",
            headers=auth_headers,
            params={"q": "API Page", "sort": "name-asc", "limit": 1},
        )

        assert response.status_code == 200, response.text
        assert [item["name"] for item in response.json()["items"]] == ["API Page Alpha"]

    def test_reports_the_total_behind_the_page(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("API Page Zulu")
        make_model("API Page Alpha")

        response = client.get(
            "/api/v1/models/page",
            headers=auth_headers,
            params={"q": "API Page", "sort": "name-asc", "limit": 1},
        )

        assert response.json()["total"] == 2

    def test_resumes_from_the_cursor_it_handed_back(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("API Page Zulu")
        make_model("API Page Alpha")
        params = {"q": "API Page", "sort": "name-asc", "limit": 1}
        cursor = client.get(
            "/api/v1/models/page", headers=auth_headers, params=params
        ).json()["next_cursor"]

        response = client.get(
            "/api/v1/models/page",
            headers=auth_headers,
            params={**params, "cursor": cursor},
        )

        assert [item["name"] for item in response.json()["items"]] == ["API Page Zulu"]

    def test_stops_handing_back_a_cursor_at_the_last_page(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("API Page Zulu")
        make_model("API Page Alpha")
        params = {"q": "API Page", "sort": "name-asc", "limit": 1}
        cursor = client.get(
            "/api/v1/models/page", headers=auth_headers, params=params
        ).json()["next_cursor"]

        response = client.get(
            "/api/v1/models/page",
            headers=auth_headers,
            params={**params, "cursor": cursor},
        )

        assert response.json()["next_cursor"] is None

    def test_refuses_a_cursor_it_did_not_issue(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get(
            "/api/v1/models/page?cursor=not-a-cursor", headers=auth_headers
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_model_cursor"

    def test_refuses_a_cursor_presented_under_a_different_sort(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("API Sort Zulu")
        make_model("API Sort Alpha")
        cursor = client.get(
            "/api/v1/models/page",
            headers=auth_headers,
            params={"q": "API Sort", "sort": "name-asc", "limit": 1},
        ).json()["next_cursor"]

        response = client.get(
            "/api/v1/models/page",
            headers=auth_headers,
            params={"q": "API Sort", "sort": "date-desc", "limit": 1, "cursor": cursor},
        )

        # Resuming under another sort would restart from a meaningless position.
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_model_cursor"

    def test_rejects_a_limit_past_the_cap(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get("/api/v1/models/page?limit=201", headers=auth_headers)

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("query", PRINTER_FILTERS)
    def test_refuses_a_printer_filter_from_an_ordinary_user(
        self, client: TestClient, user_headers, query: dict
    ) -> None:
        response = client.get(
            "/api/v1/models/page", headers=user_headers("page-ordinary"), params=query
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "admin_required"

    def test_surfaces_an_error_it_has_no_mapping_for(
        self, client: TestClient, auth_headers, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1 import models as models_api

        def unexpected(*_args: object, **_kwargs: object):
            raise ValueError("something_nobody_planned_for")

        monkeypatch.setattr(models_api.model_views, "page_items", unexpected)

        # Only the cursor error becomes a 400; anything else is a bug, and
        # reporting a bug as a client error is how it stays unfixed.
        with pytest.raises(ValueError, match="something_nobody_planned_for"):
            client.get("/api/v1/models/page", headers=auth_headers)

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/page").status_code == 401


class TestOutlinerModels:
    def test_lists_every_leaf(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Outliner Leaf")

        response = client.get("/api/v1/models/outliner", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert row.id in {item["id"] for item in response.json()}

    def test_returns_only_the_four_fields_the_tree_needs(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Outliner Leaf")

        response = client.get("/api/v1/models/outliner", headers=auth_headers)

        # This feeds a tree of thousands of leaves; anything more is bytes wasted.
        leaf = next(item for item in response.json() if item["id"] == row.id)
        assert set(leaf) == OUTLINER_FIELDS

    @pytest.mark.parametrize("query", PRINTER_FILTERS)
    def test_refuses_a_printer_filter_from_an_ordinary_user(
        self, client: TestClient, user_headers, query: dict
    ) -> None:
        response = client.get(
            "/api/v1/models/outliner",
            headers=user_headers("outliner-ordinary"),
            params=query,
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "admin_required"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/outliner").status_code == 401


class TestModelFacets:
    def test_counts_the_facets_of_the_current_filter(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("Faceted bracket")

        response = client.get("/api/v1/models/facets", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert "file_type" in response.json()

    @pytest.mark.parametrize("query", PRINTER_FILTERS)
    def test_refuses_a_printer_filter_from_an_ordinary_user(
        self, client: TestClient, user_headers, query: dict
    ) -> None:
        response = client.get(
            "/api/v1/models/facets",
            headers=user_headers("facets-ordinary"),
            params=query,
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "admin_required"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/facets").status_code == 401


class TestListTrash:
    def test_lists_a_trashed_model(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Trashed bracket", deleted_at=utcnow())

        response = client.get("/api/v1/models/trash", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert row.id in {item["id"] for item in response.json()}

    def test_leaves_out_a_model_that_is_still_live(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Live bracket")

        response = client.get("/api/v1/models/trash", headers=auth_headers)

        assert row.id not in {item["id"] for item in response.json()}

    def test_says_when_each_trashed_model_will_be_purged(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        row = make_model("Expiring bracket", deleted_at=utcnow())

        response = client.get("/api/v1/models/trash", headers=auth_headers)

        listed = next(item for item in response.json() if item["id"] == row.id)
        assert listed["expires_at"] is not None

    def test_rejects_a_limit_past_the_cap(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get("/api/v1/models/trash?limit=501", headers=auth_headers)

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/trash").status_code == 401

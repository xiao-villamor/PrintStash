"""Family browsing preserves independent Models and filters private membership."""

import pytest

from app.db.models import CollectionRole


def _drain_pages(client, headers, url, params):
    items, cursors, totals = [], set(), set()
    while True:
        response = client.get(url, headers=headers, params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        items.extend(page["items"])
        totals.add(page["total"])
        if page["next_cursor"] is None:
            return items, totals
        assert page["next_cursor"] not in cursors
        cursors.add(page["next_cursor"])
        params = {**params, "cursor": page["next_cursor"]}


class TestFamilyBrowse:
    @pytest.mark.parametrize("limit", [17, 200], ids=["small-pages", "full-pages"])
    def test_paginates_collapsed_families(
        self, client, auth_headers, make_model, make_family, make_family_member, limit
    ):
        families = [make_family("Same") for _ in range(501)]
        [
            make_family_member(family, make_model("Same"), canonical=True)
            for family in families
        ]
        models = [make_model("Same") for _ in range(2)]

        items, totals = _drain_pages(
            client,
            auth_headers,
            "/api/v1/families/browse",
            {"limit": limit, "sort": "name-asc"},
        )

        assert totals == {503}
        assert [(item["kind"], item[item["kind"]]["id"]) for item in items] == [
            *[("family", family.id) for family in families],
            *[("model", model.id) for model in models],
        ]

    def test_searches_family_name_in_collapsed_mode(
        self, client, auth_headers, make_model, make_family, make_family_member
    ):
        family = make_family("Benchy variations")
        make_family_member(family, make_model("Boat"), canonical=True)
        make_model("Unrelated")

        response = client.get("/api/v1/families/browse?q=Benchy", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["family"]["id"] == family.id
        assert response.json()["items"][0]["family"]["member_count"] == 1

    def test_searches_visible_member_in_collapsed_mode(
        self, client, auth_headers, make_model, make_family, make_family_member
    ):
        family = make_family("Variations")
        make_family_member(family, make_model("Spatula"), canonical=True)
        make_family_member(family, make_model("Long handle"))

        response = client.get("/api/v1/families/browse?q=handle", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["family"]["matching_visible_members"] == 1
        assert response.json()["items"][0]["family"]["member_count"] == 2

    def test_excludes_hidden_member_search_evidence(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        user = make_user()
        shared, hidden = make_collection("Public"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        family = make_family()
        make_family_member(family, make_model("Visible", collection=shared))
        make_family_member(
            family, make_model("Secret prototype", collection=hidden), canonical=True
        )

        response = client.get(
            "/api/v1/families/browse?q=Secret", headers=headers_for(user)
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "total": 0, "next_cursor": None}

    def test_rejects_cursor_from_other_browse_mode(
        self, client, auth_headers, make_model
    ):
        [make_model() for _ in range(2)]
        page = client.get("/api/v1/models/page?limit=1", headers=auth_headers).json()

        response = client.get(
            "/api/v1/families/browse",
            params={"cursor": page["next_cursor"]},
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "family_cursor_invalid"

    def test_projects_family_through_model_views(
        self, client, auth_headers, make_model, make_family, make_family_member
    ):
        model = make_model()
        family = make_family()
        member = make_family_member(family, model, canonical=True)

        detail = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        page = client.get("/api/v1/models/page", headers=auth_headers)

        assert detail.status_code == 200, detail.text
        assert page.status_code == 200, page.text
        assert detail.json()["family"] == page.json()["items"][0]["family"]
        assert detail.json()["family"] == {
            "id": family.id,
            "name": family.name,
            "slug": family.slug,
            "version": family.version,
            "member_id": member.id,
            "role": "canonical",
            "member_count": 1,
            "canonical_model_id": model.id,
            "effective_role": "edit",
        }

    def test_counts_only_visible_members(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        user = make_user()
        shared, hidden = make_collection("Public"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        model = make_model(collection=shared)
        family = make_family()
        make_family_member(family, model)
        make_family_member(family, make_model(collection=hidden), canonical=True)

        response = client.get(f"/api/v1/models/{model.id}", headers=headers_for(user))

        assert response.status_code == 200, response.text
        assert response.json()["family"]["member_count"] == 1
        assert response.json()["family"]["canonical_model_id"] is None

    def test_hides_membership_in_inaccessible_family(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        user = make_user()
        shared, hidden = make_collection("Public"), make_collection("Private")
        grant_role(user, shared, CollectionRole.VIEW)
        model = make_model(collection=shared)
        family = make_family(collection=hidden)
        make_family_member(family, model, canonical=True)

        response = client.get(
            "/api/v1/models/page?in_family=false", headers=headers_for(user)
        )

        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [model.id]
        assert response.json()["items"][0]["family"] is None

    @pytest.mark.parametrize(
        "query,expected_names",
        [
            pytest.param("in_family=true", ["Original", "Scaled"], id="grouped"),
            pytest.param("in_family=false", ["Ungrouped"], id="ungrouped"),
            pytest.param("family_role=canonical", ["Original"], id="canonical"),
            pytest.param("family_role=rescaled", ["Scaled"], id="role"),
            pytest.param(
                "in_family=false&family_role=canonical",
                [],
                id="contradictory-intersection",
            ),
        ],
    )
    def test_filters_models_by_family_membership(
        self,
        client,
        auth_headers,
        make_model,
        make_family,
        make_family_member,
        query,
        expected_names,
    ):
        family = make_family()
        make_family_member(family, make_model("Original"), canonical=True)
        make_family_member(family, make_model("Scaled"), role="rescaled")
        make_model("Ungrouped")

        response = client.get(
            f"/api/v1/models/page?sort=name-asc&{query}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [item["name"] for item in response.json()["items"]] == expected_names

    def test_filters_models_by_family_id(
        self, client, auth_headers, make_model, make_family, make_family_member
    ):
        family, other = make_family(), make_family()
        model = make_model()
        make_family_member(family, model, canonical=True)
        make_family_member(other, make_model(), canonical=True)

        response = client.get(
            f"/api/v1/models/page?family_id={family.id}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [model.id]

    def test_omits_trashed_family_summary(
        self, client, auth_headers, make_model, make_family, make_family_member
    ):
        model = make_model()
        family = make_family(trashed=True)
        make_family_member(family, model, canonical=True)

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["family"] is None


class TestListFamilies:
    @pytest.mark.parametrize("filter_name", ["q", "collection_id", "favorites", "tag"])
    def test_lists_families_with_own_filters(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        make_family,
        make_family_star,
        make_tag,
        tag_family,
        filter_name,
    ):
        actor = make_user(superuser=True)
        collection = make_collection("Collection")
        family = make_family("Benchy", collection=collection)
        make_family("Other")
        make_family_star(actor, family)
        tag = make_tag("Fixture")
        tag_family(family, tag)
        params = {
            "q": "Benchy",
            "collection_id": collection.id,
            "favorites": "true",
            "tag": tag.slug,
        }

        response = client.get(
            "/api/v1/families",
            params={filter_name: params[filter_name]},
            headers=headers_for(actor),
        )

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert [item["id"] for item in response.json()["items"]] == [family.id]

    def test_resolves_family_slug(self, client, auth_headers, make_family):
        family = make_family()

        response = client.get(
            f"/api/v1/families/by-slug/{family.slug}", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["id"] == family.id
        assert response.json()["slug"] == family.slug

    def test_keeps_vacant_family_card(self, client, auth_headers, make_family):
        family = make_family()

        response = client.get("/api/v1/families/browse", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["kind"] == "family"
        assert response.json()["items"][0]["family"]["id"] == family.id
        assert response.json()["items"][0]["family"]["canonical_model_id"] is None
        assert response.json()["items"][0]["family"]["cover_thumbnail_url"] is None

    def test_filters_collapsed_cards_before_paging(
        self,
        client,
        auth_headers,
        make_family,
        make_model,
        make_family_member,
        make_file,
    ):
        from app.db.models import FileType

        family = make_family()
        make_family_member(family, make_model("Original"), canonical=True)
        match = make_model("Printable")
        make_family_member(family, match)
        make_file(match, file_type=FileType.GCODE)
        make_model("Not printable")

        response = client.get(
            "/api/v1/families/browse?file_type=gcode&limit=1", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["family"]["matching_visible_members"] == 1
        assert response.json()["items"][0]["family"]["total_visible_members"] == 2
        assert response.json()["next_cursor"] is None

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"context": "other-user"}, id="context"),
            pytest.param({"kind": "multipart"}, id="kind"),
            pytest.param({"id": True}, id="boolean-id"),
            pytest.param({"id": 0}, id="nonpositive-id"),
            pytest.param({"id": 2**63}, id="overflow-id"),
            pytest.param({"value": []}, id="invalid-date"),
            pytest.param({"value": "not-a-date"}, id="malformed-date"),
        ],
    )
    def test_rejects_invalid_family_cursor(
        self, client, auth_headers, make_family, changes
    ):
        import base64
        import json

        [make_family() for _ in range(2)]
        response = client.get("/api/v1/families?limit=1", headers=auth_headers)
        cursor = response.json()["next_cursor"]
        payload = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        )
        payload.update(changes)
        invalid = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

        rejected = client.get(
            "/api/v1/families", params={"cursor": invalid}, headers=auth_headers
        )

        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["detail"] == "family_cursor_invalid"

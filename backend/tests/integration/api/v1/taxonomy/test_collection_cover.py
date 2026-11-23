"""Collection covers follow the saved selection without losing access checks.

The preview is what people see on the Library card. It must honor saved order,
ignore Models that have become unavailable, and retain the previous cover when
a write is rejected.
"""

import pytest

from app.db.models import CollectionRole

PREVIEW = "/api/v1/models/browse/collection-preview"


class TestCollectionCover:
    def test_saved_models_replace_the_automatic_collage_in_selection_order(
        self, client, auth_headers, make_collection, make_model
    ):
        folder = make_collection()
        child = make_collection(parent=folder)
        first = make_model(collection=folder)
        second = make_model(collection=child)
        make_model(collection=folder)
        url = f"/api/v1/collections/{folder.id}/cover"
        selection = {"kind": "models", "model_ids": [second.id, first.id]}

        assert client.put(url, json=selection, headers=auth_headers).status_code == 200

        assert client.get(url, headers=auth_headers).json() == selection
        lookup = client.get(
            "/api/v1/collections/lookup", params={"id": folder.id}, headers=auth_headers
        )
        assert lookup.json()["collection"]["has_custom_cover"] is True
        preview = client.get(
            PREVIEW, params={"collection": folder.path}, headers=auth_headers
        )
        assert [item["id"] for item in preview.json()["items"]] == [second.id, first.id]

    def test_automatic_reset_restores_the_default_cover(
        self, client, auth_headers, make_collection, make_model
    ):
        folder = make_collection()
        model = make_model(collection=folder)
        url = f"/api/v1/collections/{folder.id}/cover"
        assert (
            client.put(
                url,
                json={"kind": "models", "model_ids": [model.id]},
                headers=auth_headers,
            ).status_code
            == 200
        )

        assert (
            client.put(
                url, json={"kind": "automatic"}, headers=auth_headers
            ).status_code
            == 200
        )

        assert client.get(url, headers=auth_headers).json() == {"kind": "automatic"}
        lookup = client.get(
            "/api/v1/collections/lookup", params={"id": folder.id}, headers=auth_headers
        )
        assert lookup.json()["collection"]["has_custom_cover"] is False

    def test_stale_cover_model_falls_back_when_trashed(
        self, client, auth_headers, make_collection, make_model
    ):
        folder = make_collection()
        selected = make_model(collection=folder)
        remaining = make_model(collection=folder)
        url = f"/api/v1/collections/{folder.id}/cover"
        assert (
            client.put(
                url,
                json={"kind": "models", "model_ids": [selected.id]},
                headers=auth_headers,
            ).status_code
            == 200
        )
        assert (
            client.delete(
                f"/api/v1/models/{selected.id}", headers=auth_headers
            ).status_code
            == 204
        )

        preview = client.get(
            PREVIEW, params={"collection": folder.path}, headers=auth_headers
        )
        assert [item["id"] for item in preview.json()["items"]] == [remaining.id]
        assert client.get(url, headers=auth_headers).json() == {"kind": "automatic"}

    @pytest.mark.parametrize("relation", ["outside", "trashed"])
    def test_rejects_ineligible_models(
        self, relation, client, auth_headers, make_collection, make_model
    ):
        folder = make_collection()
        model = make_model(
            collection=folder
            if relation == "trashed"
            else make_collection("Elsewhere"),
            trashed=relation == "trashed",
        )

        response = client.put(
            f"/api/v1/collections/{folder.id}/cover",
            headers=auth_headers,
            json={"kind": "models", "model_ids": [model.id]},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "collection_cover_models_unavailable"

    @pytest.mark.parametrize("model_ids", [[], [1, 1], [1, 2, 3, 4, 5], [0], ["1"]])
    def test_rejects_invalid_cover_selection(
        self, model_ids, client, auth_headers, make_collection
    ):
        folder = make_collection()

        response = client.put(
            f"/api/v1/collections/{folder.id}/cover",
            json={"kind": "models", "model_ids": model_ids},
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_a_viewer_can_see_the_collage_without_editing_it(
        self,
        client,
        auth_headers,
        make_collection,
        make_model,
        make_user,
        headers_for,
        grant_role,
    ):
        folder = make_collection()
        model = make_model(collection=folder)
        viewer = make_user()
        grant_role(viewer, folder, CollectionRole.VIEW)
        viewer_headers = headers_for(viewer)
        url = f"/api/v1/collections/{folder.id}/cover"
        selection = {"kind": "models", "model_ids": [model.id]}
        assert client.put(url, json=selection, headers=auth_headers).status_code == 200

        assert client.get(url, headers=viewer_headers).status_code == 403
        assert (
            client.put(
                url, json={"kind": "automatic"}, headers=viewer_headers
            ).status_code
            == 403
        )
        preview = client.get(
            PREVIEW, params={"collection": folder.path}, headers=viewer_headers
        )
        assert [item["id"] for item in preview.json()["items"]] == [model.id]

"""Mixed Library continuation requires controlled refresh after a conditional edit."""

import pytest

from tests.factories import build_model, build_multipart_model


class TestLibraryBrowse:
    @pytest.mark.asyncio
    async def test_refreshes_library_after_conditional_edit(
        self, api, e2e_db, superuser_headers
    ):
        model = build_model(e2e_db, "A member")
        group = build_multipart_model(e2e_db, "B set")
        composed = await api.put(
            f"/api/v1/multipart-models/{group.id}/parts",
            headers=superuser_headers,
            json={"parts": [{"name": "Body", "model_ids": [model.id]}]},
        )
        assert composed.status_code == 200, composed.text
        first = await api.get(
            "/api/v1/models/browse",
            headers=superuser_headers,
            params={"limit": 1, "sort": "name-asc"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 2
        edited = await api.patch(
            f"/api/v1/models/{model.id}",
            headers={
                **superuser_headers,
                "X-PrintStash-Edit-Contract": "conditional-v1",
                "If-Match": f'"model-{model.id}-e{model.edit_epoch}-v1"',
            },
            json={"name": "Renamed"},
        )
        assert edited.status_code == 200, edited.text

        stale = await api.get(
            "/api/v1/models/browse",
            headers=superuser_headers,
            params={
                "limit": 1,
                "sort": "name-asc",
                "cursor": first.json()["next_cursor"],
            },
        )

        assert stale.status_code == 409, stale.text
        assert stale.json()["detail"] == "browse_refresh_required"
        refreshed = await api.get("/api/v1/models/browse", headers=superuser_headers)
        assert refreshed.status_code == 200, refreshed.text
        assert {
            (row["kind"], row[row["kind"]]["id"]) for row in refreshed.json()["items"]
        } == {("model", model.id), ("multipart", group.id)}

    @pytest.mark.asyncio
    async def test_reads_thumbnail_arrival_without_rebuilding_displayed_cards(
        self, api, e2e_db, superuser_headers
    ):
        model = build_model(e2e_db, "Displayed")
        page_response = await api.get(
            "/api/v1/models/browse", headers=superuser_headers
        )
        assert page_response.status_code == 200, page_response.text
        displayed = page_response.json()
        model.thumbnail_path = "123.png"
        e2e_db.add(model)
        e2e_db.commit()
        response = await api.get(
            "/api/v1/models/browse/thumbnails",
            headers=superuser_headers,
            params={"model_id": model.id},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "items": [
                {"model_id": model.id, "thumbnail_url": "/api/v1/files/123/thumbnail"}
            ],
            "authorization_revision": displayed["authorization_revision"],
        }
        assert displayed["items"][0]["model"]["thumbnail_url"] is None
        revision = await api.get(
            "/api/v1/models/browse/revision", headers=superuser_headers
        )
        assert revision.json()["browse_revision"] != displayed["browse_revision"]

    @pytest.mark.asyncio
    async def test_preserves_mixed_order_across_continuation(
        self, api, e2e_db, superuser_headers
    ):
        first = build_model(e2e_db, "A")
        tied = build_multipart_model(e2e_db, "A")
        second = build_model(e2e_db, "B")
        last = build_multipart_model(e2e_db, "C")
        cursor = None
        observed = []
        for _ in range(4):
            params = {"limit": 1, "sort": "name-asc"}
            if cursor is not None:
                params["cursor"] = cursor
            response = await api.get(
                "/api/v1/models/browse", headers=superuser_headers, params=params
            )
            assert response.status_code == 200, response.text
            body = response.json()
            observed.extend(
                (row["kind"], row[row["kind"]]["id"]) for row in body["items"]
            )
            cursor = body["next_cursor"]
        assert observed == [
            ("model", first.id),
            ("multipart", tied.id),
            ("model", second.id),
            ("multipart", last.id),
        ]
        assert cursor is None

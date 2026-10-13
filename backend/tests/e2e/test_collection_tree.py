"""E2E: browsing a nested library one level at a time, as the sidebar does.

A 9,000-collection library took a minute to open because the sidebar loaded
the whole tree at once (#295). The replacement loads the top level, a level
more on each expand, and resolves a deep link by path. This flow creates a
small nested library through the API and walks it that way, over the whole app.
"""

from __future__ import annotations

import pytest


class TestBrowseCollectionTree:
    @pytest.mark.asyncio
    async def test_walks_from_the_top_level_to_a_deep_collection(
        self, api, superuser_headers: dict[str, str]
    ) -> None:
        created = await api.post(
            "/api/v1/collections",
            json={"name": "Parts/Brackets/Small"},
            headers=superuser_headers,
        )
        assert created.status_code == 201, created.text

        roots = await api.get("/api/v1/collections/children", headers=superuser_headers)
        parts = roots.json()["items"][0]
        children = await api.get(
            "/api/v1/collections/children",
            params={"parent_id": parts["id"]},
            headers=superuser_headers,
        )
        found = await api.get(
            "/api/v1/collections/lookup",
            params={"path": "parts/brackets/small"},
            headers=superuser_headers,
        )

        assert [parts["name"], children.json()["items"][0]["name"]] == [
            "Parts",
            "Brackets",
        ]
        assert [a["name"] for a in found.json()["ancestors"]] == ["Parts", "Brackets"]

    @pytest.mark.asyncio
    async def test_pages_every_root_entry(
        self, api, superuser_headers: dict[str, str]
    ) -> None:
        created = []
        for name in ["A outliner", "B outliner", "C outliner"]:
            response = await api.post(
                "/api/v1/multipart-models",
                json={"name": name},
                headers=superuser_headers,
            )
            assert response.status_code == 201, response.text
            created.append(response.json()["id"])
        found = []
        cursor = None
        for _ in created:
            params = {"limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            response = await api.get(
                "/api/v1/outliner/entries", params=params, headers=superuser_headers
            )
            assert response.status_code == 200, response.text
            found.extend(row["id"] for row in response.json()["items"])
            cursor = response.json()["next_cursor"]
        assert found == created
        assert cursor is None

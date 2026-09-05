"""A connected Library keeps its target while credentials are edited via HTTP."""

import pytest

from tests.e2e._backup_helpers import setup_and_login


class TestConnectionEditing:
    @pytest.mark.asyncio
    async def test_edits_credentials_without_redirecting_a_linked_source(
        self, api, tmp_path, webdav_endpoint
    ):
        headers = await setup_and_login(api, tmp_path)
        enabled = await api.put(
            "/api/v1/config", headers=headers, json={"external_libraries_enabled": True}
        )
        assert enabled.status_code == 200, enabled.text
        created = await api.post(
            "/api/v1/storage-connections",
            headers=headers,
            json={
                "name": "Source connection",
                "kind": "webdav",
                "purpose": "both",
                "configuration": {
                    "provider": "webdav",
                    "endpoint_url": webdav_endpoint,
                    "username": "owner",
                    "root": "models",
                },
                "secrets": {"password": "initial-password"},
            },
        )
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        library = await api.post(
            "/api/v1/libraries",
            headers=headers,
            json={
                "name": "Linked models",
                "source_kind": "webdav",
                "connection_id": identifier,
            },
        )
        assert library.status_code == 201, library.text
        changed = await api.patch(
            f"/api/v1/storage-connections/{identifier}",
            headers=headers,
            json={
                "name": "Renamed source",
                "secrets": {"password": "replacement-password"},
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["configuration"] == created.json()["configuration"]
        assert "replacement-password" not in changed.text
        refused = await api.patch(
            f"/api/v1/storage-connections/{identifier}",
            headers=headers,
            json={"configuration": {"root": "another-folder"}},
        )
        assert refused.status_code == 409
        assert refused.json()["detail"] == "storage_connection_target_in_use"
        retained = await api.get("/api/v1/storage-connections", headers=headers)
        assert retained.json()[0]["configuration"] == created.json()["configuration"]

"""Vault configuration exposes storage guarantees without leaking secrets."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestGetConfig:
    def test_returns_the_active_storage_capabilities(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        response = client.get("/api/v1/config", headers=auth_headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["storage_tier"] == "verified"
        assert body["storage_capabilities"]["object_identity"] == "inode"
        assert body["storage_warnings"] == []

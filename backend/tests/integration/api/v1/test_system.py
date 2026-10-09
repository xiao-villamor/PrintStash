"""The system router exposes a guarded, administrator-only restart request.

Restarting is deliberately opt-in because a bare uvicorn process has no
supervisor to bring it back. When enabled, the endpoint acknowledges the
request before the graceful process signal is dispatched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import system
from app.core.config import _overlay
from tests.integration.conftest import UserHeaders


class TestRestart:
    def test_accepts_an_enabled_restart(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        dispatched: list[bool] = []
        monkeypatch.setitem(_overlay, "restart_enabled", True)
        monkeypatch.setattr(system, "request_restart", lambda: dispatched.append(True))

        response = client.post("/api/v1/system/restart", headers=auth_headers)

        assert response.status_code == 202, response.text
        assert response.json() == {"status": "restart_requested"}
        assert dispatched == [True]

    def test_refuses_a_restart_without_a_supervisor(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post("/api/v1/system/restart", headers=auth_headers)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "restart_not_enabled"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        response = client.post("/api/v1/system/restart")

        assert response.status_code == 401, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.post(
            "/api/v1/system/restart", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text


class TestComputeStatus:
    def test_reports_cpu_override(self, client, auth_headers, monkeypatch):
        monkeypatch.setitem(_overlay, "compute_mode", "cpu")

        response = client.get("/api/v1/system/compute", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["mode"] == "cpu"
        assert {item["reason"] for item in response.json()["capabilities"]} == {
            "disabled"
        }

    def test_denies_non_administrators(self, client, user_headers):
        response = client.get(
            "/api/v1/system/compute", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text

    def test_denies_unauthenticated_diagnostics(self, client):
        response = client.get("/api/v1/system/compute")

        assert response.status_code == 401

    def test_reports_missing_optional_runtime(self, client, auth_headers, monkeypatch):
        from app.runtime.compute import client as compute

        monkeypatch.setitem(_overlay, "compute_mode", "auto")
        original = compute.importlib.util.find_spec
        monkeypatch.setattr(
            compute.importlib.util,
            "find_spec",
            lambda name: None if name == "wgpu" else original(name),
        )

        response = client.get("/api/v1/system/compute", headers=auth_headers)

        assert response.status_code == 200
        assert {item["reason"] for item in response.json()["capabilities"]} == {
            "runtime_missing"
        }
        assert response.json()["reserved_bytes"] == 0

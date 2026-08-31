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

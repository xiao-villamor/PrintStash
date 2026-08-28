"""Regression coverage for API hardening contracts."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.core.config import _overlay


class TestOpenApiContract:
    def test_openapi_describes_the_actual_http_bearer_contract(
        self,
        client: TestClient,
    ) -> None:
        schema = client.get("/openapi.json").json()

        assert schema["components"]["securitySchemes"]["BearerAuth"] == {
            "type": "http",
            "scheme": "bearer",
        }
        assert "OAuth2PasswordBearer" not in schema["components"]["securitySchemes"]


class TestBodyLimit:
    def test_api_rejects_oversized_request_body_before_route(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ceiling is the per-file cap plus multipart headroom, and it still bites.

        The headroom is shrunk here rather than sending 17 MiB: what this test is
        about is that *some* body is refused before any route runs, not the size at
        which that happens.
        """
        monkeypatch.setattr(config, "MULTIPART_OVERHEAD_BYTES", 0)
        _overlay["max_upload_mb"] = 1
        try:
            response = client.post(
                "/api/v1/auth/login",
                content=b"x" * (1024 * 1024 + 1),
                headers={"Content-Type": "application/octet-stream"},
            )
        finally:
            _overlay.pop("max_upload_mb", None)

        assert response.status_code == 413
        assert response.json() == {"detail": "request_too_large"}


class TestCors:
    def test_default_cors_allows_local_dev_origin(self, client: TestClient) -> None:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert (
            response.headers["access-control-allow-origin"] == "http://localhost:3000"
        )

    def test_default_cors_rejects_unconfigured_origin(self, client: TestClient) -> None:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers


# The three health-endpoint tests that used to live here (public liveness disclosure,
# the detailed probe's auth + component reporting, and the release check's auth) moved to
# tests/integration/api/v1/test_health.py, the mirror of the module they defend. Each was
# also several behaviours in one test; they are split there.


# `test_configured_setup_status_redacts_internal_storage_details` moved to
# tests/integration/api/v1/test_setup.py, the mirror of the router it defends.


class TestSchemaStrictness:
    def test_write_payloads_reject_unknown_fields(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/printers",
            headers=auth_headers,
            json={
                "name": "Ender 3",
                "moonraker_url": "http://10.0.0.1:7125",
                "unexpected": "ignored-before-hardening",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "request_validation_failed"

    def test_unhandled_errors_return_stable_json(self, app: FastAPI) -> None:
        if not any(
            getattr(route, "path", None) == "/__test__/boom" for route in app.routes
        ):

            @app.get("/__test__/boom")
            def boom() -> None:
                raise RuntimeError("secret traceback details")

        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/__test__/boom")

        assert response.status_code == 500
        assert response.json() == {"detail": "internal_server_error"}
        assert "secret traceback details" not in response.text

    def test_malformed_json_returns_stable_validation_contract(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/auth/login",
            content=b'{"username":',
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["detail"] == "request_validation_failed"
        assert isinstance(body["errors"], list)

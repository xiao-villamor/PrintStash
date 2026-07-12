"""Regression coverage for API hardening contracts."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_config


def test_unhandled_errors_return_stable_json(app: FastAPI) -> None:
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


def test_malformed_json_returns_stable_validation_contract(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        content=b'{"username":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "request_validation_failed"
    assert isinstance(body["errors"], list)


def test_default_cors_allows_local_dev_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_default_cors_rejects_unconfigured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_health_reports_release_components(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "PrintStash"
    assert body["version"] == get_config().app_version
    assert body["components"]["database"]["ok"] is True
    assert body["components"]["storage"]["backend"] == "local"
    assert body["components"]["backup"]["s3_configured"] is False
    providers = body["components"]["printer_providers"]["providers"]
    bambu = next(p for p in providers if p["provider"] == "bambu_lan")
    assert bambu["support_level"] == "beta"
    assert bambu["capabilities"]["can_upload"] is True
    assert bambu["capabilities"]["can_start"] is True
    assert "list_files" in bambu["unsupported_actions"]
    prusalink = next(p for p in providers if p["provider"] == "prusalink")
    assert prusalink["support_level"] == "beta"
    assert prusalink["capabilities"]["can_list_files"] is True
    assert prusalink["capabilities"]["can_send_gcode"] is False
    centauri = next(p for p in providers if p["provider"] == "elegoo_centauri")
    assert centauri["support_level"] == "beta"
    assert centauri["capabilities"]["can_live_status"] is True
    assert centauri["capabilities"]["can_upload"] is False


def test_write_payloads_reject_unknown_fields(
    client: TestClient, auth_headers: dict[str, str]
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

"""Defends http contract at the health API integration boundary.

A regression could hide an unhealthy service or accept an invalid health request.
"""

from __future__ import annotations

from ._hardening_shared import (
    FastAPI,
    TestClient,
    _overlay,
)


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


def test_openapi_describes_the_actual_http_bearer_contract(
    client: TestClient,
) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert "OAuth2PasswordBearer" not in schema["components"]["securitySchemes"]


def test_api_rejects_oversized_request_body_before_route(client: TestClient) -> None:
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

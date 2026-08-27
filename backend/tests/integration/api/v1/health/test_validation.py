"""Defends validation at the health API integration boundary.

A regression could hide an unhealthy service or accept an invalid health request.
"""

from __future__ import annotations

from ._hardening_shared import (
    TestClient,
)


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

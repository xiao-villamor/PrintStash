"""Release health uses bounded cached egress and remains administrator-only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.services import release_check

from .._auth_shared import create_user, headers


def _release(version: str) -> dict:
    return {
        "status": "update_available",
        "current_version": "0.12.1",
        "latest_version": version,
        "update_available": True,
        "release_url": f"https://example.test/releases/{version}",
        "published_at": "2026-08-01T00:00:00Z",
        "checked_at": "2026-08-26T00:00:00Z",
    }


def test_returns_latest_release_metadata(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_check._cache.clear()  # noqa: SLF001 - cache contract

    async def fetch(_current_version: str) -> dict:
        return _release("0.13.0")

    monkeypatch.setattr(release_check, "_fetch_release_status", fetch)

    response = client.get("/api/v1/health/releases/latest", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["latest_version"] == "0.13.0"
    assert response.json()["update_available"] is True


def test_caches_latest_release_metadata(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_check._cache.clear()  # noqa: SLF001 - cache contract
    versions = iter(("0.13.0", "0.14.0"))

    async def fetch(_current_version: str) -> dict:
        return _release(next(versions))

    monkeypatch.setattr(release_check, "_fetch_release_status", fetch)
    first = client.get("/api/v1/health/releases/latest", headers=auth_headers)
    assert first.status_code == 200, first.text

    response = client.get("/api/v1/health/releases/latest", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["latest_version"] == "0.13.0"


def test_refreshes_latest_release_metadata_on_request(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_check._cache.clear()  # noqa: SLF001 - cache contract
    versions = iter(("0.13.0", "0.14.0"))

    async def fetch(_current_version: str) -> dict:
        return _release(next(versions))

    monkeypatch.setattr(release_check, "_fetch_release_status", fetch)
    first = client.get("/api/v1/health/releases/latest", headers=auth_headers)
    assert first.status_code == 200, first.text

    response = client.get(
        "/api/v1/health/releases/latest?refresh=true", headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["latest_version"] == "0.14.0"


def test_degrades_safely_when_release_lookup_fails(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_check._cache.clear()  # noqa: SLF001 - cache contract

    async def fetch(current_version: str) -> dict:
        return release_check._unavailable(current_version)  # noqa: SLF001

    monkeypatch.setattr(release_check, "_fetch_release_status", fetch)

    response = client.get("/api/v1/health/releases/latest", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unavailable"
    assert response.json()["latest_version"] is None


def test_denies_a_non_superuser_from_release_lookup(
    client: TestClient, db_session: Session
) -> None:
    caller = create_user(db_session, "release-regular-user")

    response = client.get("/api/v1/health/releases/latest", headers=headers(caller))

    assert response.status_code == 403, response.text


def test_denies_an_unauthenticated_caller_from_release_lookup(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/health/releases/latest")

    assert response.status_code == 401, response.text

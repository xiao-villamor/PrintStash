"""External-library CRUD, access control, defaults, and scan-path behaviours."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import ExternalLibrary, User
from app.services.auth import create_access_token, hash_password
from tests.integration.api.v1.external_libraries._external_libraries_shared import (
    _enable_feature,
    _make_library,
)


def _regular_headers(session: Session) -> dict[str, str]:
    user = User(
        username="external-library-regular",
        hashed_password=hash_password("Password123"),
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "Authorization": f"Bearer {create_access_token(user.id, user.username, scope='write')}"
    }


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("get", "/api/v1/libraries", {}, id="list"),
        pytest.param(
            "post",
            "/api/v1/libraries",
            {"json": {"name": "nas", "root_path": "/tmp"}},
            id="create",
        ),
        pytest.param(
            "patch",
            "/api/v1/libraries/999",
            {"json": {"enabled": False}},
            id="update",
        ),
        pytest.param("delete", "/api/v1/libraries/999", {}, id="delete"),
        pytest.param("post", "/api/v1/libraries/999/scan", {}, id="scan"),
        pytest.param(
            "post",
            "/api/v1/libraries/999/scan-path",
            {"json": {"path": ""}},
            id="scan-path",
        ),
    ],
)
def test_feature_disabled_hides_every_external_library_route(
    method: str,
    path: str,
    kwargs: dict,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.request(method, path, headers=auth_headers, **kwargs)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "feature_disabled"


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("get", "/api/v1/libraries", {}, id="list"),
        pytest.param(
            "post",
            "/api/v1/libraries",
            {"json": {"name": "nas", "root_path": "/tmp"}},
            id="create",
        ),
        pytest.param(
            "patch",
            "/api/v1/libraries/999",
            {"json": {"enabled": False}},
            id="update",
        ),
        pytest.param("delete", "/api/v1/libraries/999", {}, id="delete"),
        pytest.param("post", "/api/v1/libraries/999/scan", {}, id="scan"),
        pytest.param(
            "post",
            "/api/v1/libraries/999/scan-path",
            {"json": {"path": ""}},
            id="scan-path",
        ),
    ],
)
def test_requires_superuser_for_every_external_library_route(
    method: str,
    path: str,
    kwargs: dict,
    client: TestClient,
    db_session: Session,
) -> None:
    _enable_feature(db_session)

    response = client.request(
        method, path, headers=_regular_headers(db_session), **kwargs
    )

    assert response.status_code == 403, response.text
    assert db_session.exec(select(ExternalLibrary)).all() == []


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("get", "/api/v1/libraries", {}, id="list"),
        pytest.param(
            "post",
            "/api/v1/libraries",
            {"json": {"name": "nas", "root_path": "/tmp"}},
            id="create",
        ),
        pytest.param(
            "patch",
            "/api/v1/libraries/999",
            {"json": {"enabled": False}},
            id="update",
        ),
        pytest.param("delete", "/api/v1/libraries/999", {}, id="delete"),
        pytest.param("post", "/api/v1/libraries/999/scan", {}, id="scan"),
        pytest.param(
            "post",
            "/api/v1/libraries/999/scan-path",
            {"json": {"path": ""}},
            id="scan-path",
        ),
    ],
)
def test_requires_authentication_for_every_external_library_route(
    method: str,
    path: str,
    kwargs: dict,
    client: TestClient,
    db_session: Session,
) -> None:
    _enable_feature(db_session)

    response = client.request(method, path, **kwargs)

    assert response.status_code == 401, response.text
    assert db_session.exec(select(ExternalLibrary)).all() == []


def test_create_library_applies_documented_defaults(
    tmp_path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _enable_feature(db_session)
    root = tmp_path / "nas"
    root.mkdir()

    response = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={"name": "nas", "root_path": str(root)},
    )

    assert response.status_code == 201, response.text
    assert response.json()["enabled"] is True
    assert response.json()["scan_schedule"] == "0 * * * *"
    assert response.json()["watch_mode"] == "auto"
    assert response.json()["collection_mode"] == "mirror"


def test_list_libraries_returns_configured_state(
    tmp_path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _enable_feature(db_session)
    root = tmp_path / "listed-nas"
    root.mkdir()
    library = _make_library(db_session, root, enabled=False)

    response = client.get("/api/v1/libraries", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()[0]["id"] == library.id
    assert response.json()[0]["root_path"] == str(root)
    assert response.json()[0]["enabled"] is False


def test_create_library_persists_explicit_configuration(
    tmp_path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _enable_feature(db_session)
    root = tmp_path / "explicit-nas"
    root.mkdir()

    response = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={
            "name": "Explicit NAS",
            "root_path": str(root),
            "enabled": False,
            "scan_schedule": "0 0 * * *",
            "watch_mode": "off",
            "collection_mode": "single",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Explicit NAS"
    assert response.json()["scan_schedule"] == "0 0 * * *"
    assert response.json()["watch_mode"] == "off"
    assert response.json()["collection_mode"] == "single"


def test_create_library_rejects_a_duplicate_normalized_root(
    tmp_path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _enable_feature(db_session)
    root = tmp_path / "nas"
    root.mkdir()
    _make_library(db_session, root)

    response = client.post(
        "/api/v1/libraries",
        headers=auth_headers,
        json={"name": "duplicate", "root_path": str(root / ".")},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "root_path_overlaps_managed_storage"
    assert len(db_session.exec(select(ExternalLibrary)).all()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"name": "", "root_path": "/tmp"}, id="empty-name"),
        pytest.param(
            {"name": "nas", "root_path": "/tmp", "watch_mode": "sometimes"},
            id="invalid-watch-mode",
        ),
        pytest.param(
            {"name": "nas", "root_path": "/tmp", "collection_mode": "mixed"},
            id="invalid-collection-mode",
        ),
    ],
)
def test_create_library_rejects_schema_bounds_and_enums(
    payload: dict,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _enable_feature(db_session)

    response = client.post("/api/v1/libraries", headers=auth_headers, json=payload)

    assert response.status_code == 422, response.text
    assert db_session.exec(select(ExternalLibrary)).all() == []


def test_empty_partial_path_scans_the_library_root(
    tmp_path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _enable_feature(db_session)
    root = tmp_path / "nas"
    root.mkdir()
    library = _make_library(db_session, root)

    response = client.post(
        f"/api/v1/libraries/{library.id}/scan-path",
        headers=auth_headers,
        json={},
    )

    assert response.status_code == 202, response.text
    assert response.json()["state"] == "pending"
    assert response.json()["message"] == "folder scan queued"

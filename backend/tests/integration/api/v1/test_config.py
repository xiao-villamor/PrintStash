"""Currency setting round-trips through the config API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import File, FileType, Model


def _configure_storage(tmp_path: Path) -> None:
    # PUT /config calls ensure_dirs(); point storage at the test's tmp dir so it
    # doesn't try to mkdir the real /data root.
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"


def test_currency_defaults_to_usd(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/config", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["currency"] == "USD"


def test_currency_can_be_updated(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    resp = client.put("/api/v1/config", json={"currency": "EUR"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["currency"] == "EUR"

    # Persisted across reads.
    assert (
        client.get("/api/v1/config", headers=auth_headers).json()["currency"] == "EUR"
    )


def test_currency_rejects_bad_length(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put("/api/v1/config", json={"currency": "EURO"}, headers=auth_headers)
    assert resp.status_code == 422


def test_model_thumbnail_quality_round_trips_and_rejects_unknown_presets(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    _configure_storage(tmp_path)
    response = client.put(
        "/api/v1/config",
        json={"model_thumbnail_width": 1280},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["model_thumbnail_width"] == 1280
    assert (
        client.get("/api/v1/config", headers=auth_headers).json()[
            "model_thumbnail_width"
        ]
        == 1280
    )

    invalid = client.put(
        "/api/v1/config",
        json={"model_thumbnail_width": 900},
        headers=auth_headers,
    )
    assert invalid.status_code == 422


def test_update_rejects_invalid_storage_backend(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put(
        "/api/v1/config", json={"storage_backend": "ftp"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert "storage_backend" in resp.json()["detail"]


def test_update_rejects_storage_remap_once_artifacts_exist(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
) -> None:
    _configure_storage(tmp_path)
    current = Path(_overlay["data_dir"])
    current.mkdir(parents=True)
    blob = current / "model" / "v1" / "part.stl"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"owned")
    model = Model(name="Owned", slug="owned", hash="a" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    db_session.add(
        File(
            model_id=model.id,
            path=str(blob),
            original_filename="part.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=5,
            sha256="b" * 64,
        )
    )
    db_session.commit()

    response = client.put(
        "/api/v1/config",
        json={"data_dir": str(tmp_path / "other")},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "storage_migration_required"
    assert Path(_overlay["data_dir"]) == current
    assert blob.read_bytes() == b"owned"


def test_update_toggles_auto_mark_known_good_and_external_libraries(
    client: TestClient, auth_headers: dict[str, str], tmp_path
) -> None:
    _configure_storage(tmp_path)
    resp = client.put(
        "/api/v1/config",
        json={"auto_mark_known_good": False, "external_libraries_enabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auto_mark_known_good"] is False
    assert body["external_libraries_enabled"] is True


# --------------------------------------------------------------------------- #
# MakerWorld login/status endpoints
# --------------------------------------------------------------------------- #


def test_makerworld_status_defaults_disconnected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.get("/api/v1/config/makerworld", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "updated_at": None}


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            "/api/v1/config/makerworld/login",
            {"account": "user@example.com", "password": "secret"},
        ),
        (
            "/api/v1/config/makerworld/verify",
            {"login_token": "pending", "code": "123456"},
        ),
        ("/api/v1/config/makerworld/token", {"token": "legacy-token"}),
    ],
)
def test_makerworld_connection_mutations_require_extension(
    client: TestClient,
    auth_headers: dict[str, str],
    path: str,
    payload: dict[str, str],
) -> None:
    resp = client.post(path, json=payload, headers=auth_headers)
    assert resp.status_code == 410
    assert resp.json()["detail"] == "makerworld_extension_required"


def test_makerworld_disconnect_remains_compatible(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.delete("/api/v1/config/makerworld", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "updated_at": None}

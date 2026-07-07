"""Currency setting round-trips through the config API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import _overlay


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
    resp = client.put(
        "/api/v1/config", json={"currency": "EUR"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["currency"] == "EUR"

    # Persisted across reads.
    assert client.get("/api/v1/config", headers=auth_headers).json()["currency"] == "EUR"


def test_currency_rejects_bad_length(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.put(
        "/api/v1/config", json={"currency": "EURO"}, headers=auth_headers
    )
    assert resp.status_code == 422

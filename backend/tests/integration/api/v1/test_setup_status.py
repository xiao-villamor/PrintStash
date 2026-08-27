"""Integration coverage for first-run setup status."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import _overlay
from app.services.setup_token import current_setup_token


def _isolate_runtime_dirs(tmp_path: Path) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"


class TestGetStatus:
    def test_reports_an_unconfigured_vault(self, client: TestClient) -> None:
        response = client.get("/api/v1/setup/status")

        assert response.status_code == 200, response.text
        assert response.json()["configured"] is False
        assert response.json()["user_count"] == 0

    def test_returns_safe_setup_defaults(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _isolate_runtime_dirs(tmp_path)

        response = client.get("/api/v1/setup/status")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["setup_token_required"] is True
        assert body["default_data_dir"]
        assert body["default_thumb_dir"]
        assert body["current_storage_backend"] == "local"

    def test_omits_setup_and_storage_secrets_from_status(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _isolate_runtime_dirs(tmp_path)
        _overlay["s3_access_key"] = "asset-access-secret"
        _overlay["s3_secret_key"] = "asset-secret-value"
        _overlay["backup_s3_access_key"] = "backup-access-secret"
        _overlay["backup_s3_secret_key"] = "backup-secret-value"

        response = client.get("/api/v1/setup/status")

        serialized = response.text
        assert response.status_code == 200, serialized
        assert "asset-access-secret" not in serialized
        assert "asset-secret-value" not in serialized
        assert "backup-access-secret" not in serialized
        assert "backup-secret-value" not in serialized
        assert current_setup_token() not in serialized

    def test_reports_a_configured_vault(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        _isolate_runtime_dirs(tmp_path)
        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "configured-admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )
        assert response.status_code == 201, response.text

        status_response = client.get("/api/v1/setup/status")

        assert status_response.status_code == 200, status_response.text
        assert status_response.json() == {"configured": True, "user_count": 0}

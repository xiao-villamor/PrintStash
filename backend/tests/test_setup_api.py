from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import SystemConfig, User
from app.services import runtime_config
from app.services.setup_token import current_setup_token


class TestFirstRunSetup:
    def _isolate_runtime_dirs(self, tmp_path: Path) -> None:
        _overlay["staging_dir"] = tmp_path / "staging"
        _overlay["backup_dir"] = tmp_path / "backups"
        _overlay["data_dir"] = tmp_path / "files"
        _overlay["thumb_dir"] = tmp_path / "thumbs"

    def test_setup_persists_s3_storage_and_backup_choices(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ):
        self._isolate_runtime_dirs(tmp_path)

        resp = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "email": "admin@example.com",
                "storage_backend": "s3",
                "s3_bucket": "vault-assets",
                "s3_endpoint_url": "https://r2.example.com",
                "s3_region": "auto",
                "s3_access_key": "asset-key",
                "s3_secret_key": "asset-secret",
                "backup_retention_days": 14,
                "backup_s3_bucket": "vault-backups",
                "backup_s3_endpoint_url": "https://backup-r2.example.com",
                "backup_s3_region": "auto",
                "backup_s3_access_key": "backup-key",
                "backup_s3_secret_key": "backup-secret",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["storage_backend"] == "s3"
        assert body["access_token"]

        cfg = db_session.get(SystemConfig, 1)
        assert cfg is not None
        assert cfg.storage_backend == "s3"
        assert cfg.s3_bucket == "vault-assets"
        assert cfg.backup_retention_days == 14
        assert cfg.backup_s3_bucket == "vault-backups"

    def test_setup_requires_bucket_when_s3_selected(
        self, client: TestClient, tmp_path: Path
    ):
        self._isolate_runtime_dirs(tmp_path)

        resp = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "storage_backend": "s3",
            },
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "s3_bucket_required"

    def test_repeated_setup_submission_does_not_duplicate_state(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ):
        self._isolate_runtime_dirs(tmp_path)
        payload = {
            "setup_token": current_setup_token(),
            "username": "admin",
            "password": "Password123",
            "storage_backend": "local",
        }

        first = client.post("/api/v1/setup", json=payload)
        second = client.post("/api/v1/setup", json=payload)

        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["detail"] == "already_configured"
        assert len(db_session.exec(select(User)).all()) == 1

    def test_setup_rejects_request_without_operator_token(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ):
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": "attacker-controlled-token",
                "username": "attacker",
                "password": "Password123",
                "storage_backend": "local",
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "invalid_setup_token"
        assert db_session.exec(select(User)).first() is None

    def test_setup_rolls_back_config_and_user_when_finalization_fails(
        self,
        client: TestClient,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        overlay_before = dict(_overlay)

        def fail_finalization(*_args, **_kwargs):
            raise RuntimeError("injected setup failure")

        monkeypatch.setattr(runtime_config, "mark_configured", fail_finalization)

        with pytest.raises(RuntimeError, match="injected setup failure"):
            client.post(
                "/api/v1/setup",
                json={
                    "setup_token": current_setup_token(),
                    "username": "admin",
                    "password": "Password123",
                    "storage_backend": "s3",
                    "s3_bucket": "must-rollback",
                },
            )

        db_session.expire_all()
        assert db_session.exec(select(User)).first() is None
        assert db_session.get(SystemConfig, 1) is None
        assert _overlay == overlay_before

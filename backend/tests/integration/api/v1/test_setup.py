"""Defends ``TestFirstRunSetup`` behavior for the ``v1`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.v1 import setup as setup_api
from app.core.config import _overlay
from app.db.models import SystemConfig, User
from app.db.session import get_session_factory
from app.schemas.setup import SetupRequest
from app.services import runtime_config
from app.services.setup_token import current_setup_token


def _isolate_runtime_dirs(tmp_path: Path) -> None:
    _overlay["staging_dir"] = tmp_path / "staging"
    _overlay["backup_dir"] = tmp_path / "backups"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"


class TestFirstRunSetup:
    def _isolate_runtime_dirs(self, tmp_path: Path) -> None:
        _isolate_runtime_dirs(tmp_path)

    def test_completes_first_run_setup(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "first-admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["configured"] is True
        assert response.json()["username"] == "first-admin"

    def test_creates_exactly_one_first_superuser(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "sole-first-admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )

        assert response.status_code == 201, response.text
        users = db_session.exec(select(User)).all()
        assert len(users) == 1
        assert users[0].is_superuser is True
        assert users[0].is_active is True

    def test_issues_an_authenticated_session_after_setup(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "session-admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )
        assert response.status_code == 201, response.text

        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
        )

        assert me.status_code == 200, me.text
        assert me.json()["username"] == "session-admin"

    def test_persists_selected_local_storage_settings(
        self,
        client: TestClient,
        db_session: Session,
        tmp_path: Path,
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        data_dir = tmp_path / "chosen-files"
        thumb_dir = tmp_path / "chosen-thumbs"

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "local-admin",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(data_dir),
                "thumb_dir": str(thumb_dir),
            },
        )

        assert response.status_code == 201, response.text
        config = db_session.get(SystemConfig, 1)
        assert config is not None
        assert config.data_dir == str(data_dir.resolve())
        assert config.thumb_dir == str(thumb_dir.resolve())

    def test_accepts_optional_email_omission(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "no-email-admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )

        assert response.status_code == 201, response.text
        user = db_session.exec(select(User)).one()
        assert user.email is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("setup_token", "short", id="short-setup-token"),
            pytest.param("username", "ab", id="short-username"),
            pytest.param("password", "short", id="short-password"),
            pytest.param("username", "u" * 129, id="long-username"),
            pytest.param("password", "p" * 257, id="long-password"),
        ],
    )
    def test_validates_setup_credential_boundaries(
        self,
        client: TestClient,
        db_session: Session,
        tmp_path: Path,
        field: str,
        value: str,
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        payload = {
            "setup_token": current_setup_token(),
            "username": "boundary-admin",
            "password": "Password123",
            "storage_backend": "local",
        }
        payload[field] = value

        response = client.post("/api/v1/setup", json=payload)

        assert response.status_code == 422, response.text
        assert db_session.exec(select(User)).first() is None

    def test_rejects_an_unsupported_storage_backend(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "backend-admin",
                "password": "Password123",
                "storage_backend": "ftp",
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "invalid_storage_backend"
        assert db_session.exec(select(User)).first() is None

    def test_validates_backup_retention_lower_boundary(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "retention-admin",
                "password": "Password123",
                "storage_backend": "local",
                "backup_retention_days": -1,
            },
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(User)).first() is None

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

    def test_persists_s3_settings_without_exposing_secrets(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        access_key = "setup-asset-access"
        secret_key = "setup-asset-secret"
        setup_response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "s3-admin",
                "password": "Password123",
                "storage_backend": "s3",
                "s3_bucket": "vault-assets",
                "s3_access_key": access_key,
                "s3_secret_key": secret_key,
            },
        )
        assert setup_response.status_code == 201, setup_response.text

        config_response = client.get(
            "/api/v1/config",
            headers={
                "Authorization": f"Bearer {setup_response.json()['access_token']}"
            },
        )

        assert config_response.status_code == 200, config_response.text
        assert config_response.json()["s3_bucket"] == "vault-assets"
        assert config_response.json()["has_s3_access_key"] is True
        assert config_response.json()["has_s3_secret_key"] is True
        assert access_key not in config_response.text
        assert secret_key not in config_response.text

    def test_persists_backup_retention_and_backup_target_settings(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "backup-admin",
                "password": "Password123",
                "storage_backend": "s3",
                "s3_bucket": "vault-assets",
                "backup_retention_days": 14,
                "backup_s3_bucket": "vault-backups",
                "backup_s3_endpoint_url": "https://backup.example.test",
                "backup_s3_region": "auto",
            },
        )

        assert response.status_code == 201, response.text
        config = db_session.get(SystemConfig, 1)
        assert config is not None
        assert config.backup_retention_days == 14
        assert config.backup_s3_bucket == "vault-backups"
        assert config.backup_s3_endpoint_url == "https://backup.example.test"

    def test_setup_persists_typed_provider_without_returning_secrets(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "provider-admin",
                "password": "Password123",
                "storage_provider": "cloudflare_r2",
                "storage_provider_config": {
                    "provider": "cloudflare_r2",
                    "bucket": "models",
                    "account_id": "account-123",
                    "access_key": "access-secret",
                    "secret_key": "key-secret",
                    "root": "printstash",
                },
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["storage_provider"] == "cloudflare_r2"
        row = db_session.get(SystemConfig, 1)
        assert row is not None
        assert row.storage_provider == "cloudflare_r2"
        assert "access-secret" not in (row.storage_provider_config_json or "")
        assert row.s3_endpoint_url == "https://account-123.r2.cloudflarestorage.com"

    def test_setup_rejects_mixed_new_and_legacy_storage_input(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "provider-admin",
                "password": "Password123",
                "storage_backend": "local",
                "storage_provider": "local",
                "storage_provider_config": {
                    "provider": "local",
                    "data_dir": str(tmp_path / "files"),
                    "thumb_dir": str(tmp_path / "thumbs"),
                },
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "mixed_storage_provider_input"

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

    def test_makes_concurrent_setup_single_winner(
        self,
        file_backed_integration_db: None,
        tmp_path: Path,
    ) -> None:
        del file_backed_integration_db
        self._isolate_runtime_dirs(tmp_path)
        token = current_setup_token()
        factory = get_session_factory()

        def submit(username: str) -> int:
            with factory.scoped_session() as session:
                body = SetupRequest.model_validate(
                    {
                        "setup_token": token,
                        "username": username,
                        "password": "Password123",
                        "storage_backend": "local",
                    }
                )
                try:
                    setup_api.complete_setup(body, Response(), session)
                except HTTPException as exc:
                    return exc.status_code
                return 201

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, ("race-admin-a", "race-admin-b")))

        assert sorted(statuses) == [201, 409]
        with get_session_factory().scoped_session() as session:
            assert len(session.exec(select(User)).all()) == 1

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

    def test_setup_rejects_populated_default_data_directory(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """A mounted model library is not a safe private vault directory.

        The frontend omits unchanged default paths, so setup must validate the
        effective path rather than only explicit request overrides.
        """
        self._isolate_runtime_dirs(tmp_path)
        existing = Path(_overlay["data_dir"]) / "Jonathan" / "part.stl"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"user-owned")

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "data_dir_not_empty"
        assert existing.read_bytes() == b"user-owned"
        assert db_session.exec(select(User)).first() is None

    def test_setup_rejects_nested_or_aliased_storage_roots(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        shared = tmp_path / "shared"

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(shared),
                "thumb_dir": str(shared / "thumbs"),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "storage_paths_overlap"
        assert db_session.exec(select(User)).first() is None

    def test_setup_rejects_symlink_alias_between_storage_roots(
        self, client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)
        shared = tmp_path / "shared"
        shared.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(shared, target_is_directory=True)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(shared),
                "thumb_dir": str(alias),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "storage_paths_overlap"
        assert db_session.exec(select(User)).first() is None

    @pytest.mark.parametrize("managed_role", ["staging_dir", "backup_dir"])
    def test_setup_rejects_data_root_overlapping_managed_scratch_root(
        self,
        client: TestClient,
        db_session: Session,
        tmp_path: Path,
        managed_role: str,
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "storage_backend": "local",
                "data_dir": str(_overlay[managed_role]),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "storage_paths_overlap"
        assert db_session.exec(select(User)).first() is None

    def test_setup_rejects_read_only_effective_root_before_db_mutation(
        self,
        client: TestClient,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._isolate_runtime_dirs(tmp_path)

        def denied(*_args, **_kwargs):
            raise PermissionError("read-only mount")

        monkeypatch.setattr("app.api.v1.setup.tempfile.NamedTemporaryFile", denied)
        response = client.post(
            "/api/v1/setup",
            json={
                "setup_token": current_setup_token(),
                "username": "admin",
                "password": "Password123",
                "storage_backend": "local",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "data_dir_not_writable"
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

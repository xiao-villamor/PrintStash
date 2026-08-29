"""Storage-provider and persisted storage override integration tests.

These tests defend typed provider persistence, secret handling, and the legacy
storage settings seam used by local-first installations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.services import runtime_config
from app.services.storage_providers import SFTPProviderConfig


@pytest.fixture(autouse=True)
def _clean_overlay():
    saved = dict(_overlay)
    yield
    _overlay.clear()
    _overlay.update(saved)


class TestUpdateStorageProvider:
    def test_rejects_a_provider_payload_with_a_different_discriminator(
        self, db_session: Session
    ) -> None:
        with pytest.raises(ValueError, match="storage_provider_config_mismatch"):
            runtime_config.update_storage_provider(
                db_session,
                provider="local",
                raw_config={"provider": "s3"},
            )

    def test_projects_s3_compatibility_fields_without_runtime_application(
        self, db_session: Session
    ) -> None:
        config = runtime_config.update_storage_provider(
            db_session,
            provider="s3",
            raw_config={
                "provider": "s3",
                "bucket": "models",
                "endpoint_url": "https://s3.example.test",
                "region": "us-east-1",
                "access_key": "fake-access",
                "secret_key": "fake-secret",
                "root": "vault",
            },
            commit=False,
            apply_runtime=False,
        )

        assert config.storage_backend == "s3"
        assert config.s3_bucket == "models"
        assert config.s3_endpoint_url == "https://s3.example.test"
        assert config.s3_region == "us-east-1"
        assert config.s3_access_key == "fake-access"
        assert config.s3_secret_key == "fake-secret"
        assert "storage_provider" not in _overlay


class TestResolveRequestedStorageProvider:
    def test_same_sftp_provider_keeps_password_authentication(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "sftp"
        config.storage_provider_secret_json = json.dumps({"password": "old-password"})
        db_session.add(config)
        db_session.commit()

        parsed = runtime_config.resolve_requested_storage_provider(
            config,
            provider="sftp",
            raw_config={
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "fake-user",
                "password": "new-password",
                "root": "models",
            },
        )

        assert isinstance(parsed, SFTPProviderConfig)
        assert parsed.password == "new-password"
        assert parsed.private_key_path == ""

    def test_same_sftp_provider_switches_to_private_key_authentication(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "sftp"
        config.storage_provider_secret_json = json.dumps({"password": "old-password"})
        db_session.add(config)
        db_session.commit()

        parsed = runtime_config.resolve_requested_storage_provider(
            config,
            provider="sftp",
            raw_config={
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "fake-user",
                "private_key_path": "/run/keys/fake_ed25519",
                "passphrase": "fake-passphrase",
                "root": "models",
            },
        )

        assert isinstance(parsed, SFTPProviderConfig)
        assert parsed.password == ""
        assert parsed.private_key_path == "/run/keys/fake_ed25519"

    def test_same_sftp_provider_preserves_existing_auth_when_auth_is_omitted(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "sftp"
        config.storage_provider_secret_json = json.dumps({"password": "old-password"})
        db_session.add(config)
        db_session.commit()

        parsed = runtime_config.resolve_requested_storage_provider(
            config,
            provider="sftp",
            raw_config={
                "provider": "sftp",
                "host": "nas.example.test",
                "username": "fake-user",
                "root": "models",
            },
        )

        assert isinstance(parsed, SFTPProviderConfig)
        assert parsed.password == "old-password"


class TestSanitizedStorageProvider:
    def test_returns_sanitized_provider_fields(self, db_session: Session) -> None:
        runtime_config.update_storage_provider(
            db_session,
            provider="s3",
            raw_config={
                "provider": "s3",
                "bucket": "models",
                "endpoint_url": "https://s3.example.test",
                "region": "us-east-1",
                "access_key": "fake-access",
                "secret_key": "fake-secret",
                "root": "vault",
            },
        )

        result = runtime_config.get_sanitized_storage_provider(db_session)

        assert result is not None
        provider, sanitized = result
        assert provider == "s3"
        assert sanitized["bucket"] == "models"
        assert sanitized["secret_fields_set"] == ["access_key", "secret_key"]
        assert "access_key" not in sanitized
        assert "secret_key" not in sanitized


class TestUpdateStorage:
    def test_update_storage_does_not_create_missing_managed_roots(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "missing-files"
        thumb_dir = tmp_path / "missing-thumbs"

        runtime_config.update_storage(
            db_session, data_dir=str(data_dir), thumb_dir=str(thumb_dir)
        )

        assert not data_dir.exists()
        assert not thumb_dir.exists()

    def test_update_storage_persists_paths(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)

        config = runtime_config.update_storage(
            db_session,
            data_dir=str(tmp_path / "files"),
            thumb_dir=str(tmp_path / "thumbs"),
        )

        assert config.data_dir == str(tmp_path / "files")
        assert config.thumb_dir == str(tmp_path / "thumbs")

    def test_update_storage_refreshes_runtime_paths(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ensure_dirs_called = False

        def ensure_dirs() -> None:
            nonlocal ensure_dirs_called
            ensure_dirs_called = True

        monkeypatch.setattr(runtime_config, "ensure_dirs", ensure_dirs)

        runtime_config.update_storage(
            db_session,
            data_dir=str(tmp_path / "files"),
            thumb_dir=str(tmp_path / "thumbs"),
        )

        assert _overlay["data_dir"] == tmp_path / "files"
        assert _overlay["thumb_dir"] == tmp_path / "thumbs"
        assert ensure_dirs_called is True

    def test_update_storage_accepts_a_thumb_path_without_a_data_path(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)

        config = runtime_config.update_storage(
            db_session, thumb_dir=str(tmp_path / "thumbs")
        )

        assert config.data_dir is None
        assert config.thumb_dir == str(tmp_path / "thumbs")
        assert _overlay["thumb_dir"] == tmp_path / "thumbs"

    def test_update_storage_accepts_no_path_overrides(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)

        config = runtime_config.update_storage(db_session)

        assert config.data_dir is None
        assert config.thumb_dir is None


class TestUpdateConfig:
    def test_update_config_int_field_clear_falls_back_to_env_default(
        self, db_session: Session
    ) -> None:
        runtime_config.update_config(db_session, backup_retention_days=45)
        assert _overlay["backup_retention_days"] == 45

        runtime_config.update_config(db_session, backup_retention_days=-1)

        config = runtime_config.get_or_create(db_session)
        assert config.backup_retention_days is None
        assert isinstance(_overlay["backup_retention_days"], int)

    def test_update_config_str_field_empty_string_clears_override(
        self, db_session: Session
    ) -> None:
        runtime_config.update_config(
            db_session, oidc_issuer_url="https://idp.example.test"
        )
        assert _overlay["oidc_issuer_url"] == "https://idp.example.test"

        runtime_config.update_config(db_session, oidc_issuer_url="")

        config = runtime_config.get_or_create(db_session)
        assert config.oidc_issuer_url is None

    def test_update_config_bool_field_round_trips(self, db_session: Session) -> None:
        runtime_config.update_config(db_session, oidc_enabled=True)
        assert _overlay["oidc_enabled"] is True
        config = runtime_config.get_or_create(db_session)
        assert config.oidc_enabled is True

        runtime_config.update_config(db_session, oidc_enabled=False)

        assert _overlay["oidc_enabled"] is False

    def test_update_config_data_dir_clear_uses_env_default_path(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)

        runtime_config.update_config(db_session, data_dir="/custom/data")
        assert _overlay["data_dir"] == Path("/custom/data")

        runtime_config.update_config(db_session, data_dir="")

        assert isinstance(_overlay["data_dir"], Path)

    def test_update_config_can_persist_without_applying_the_runtime_overlay(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)
        _overlay.pop("oidc_enabled", None)

        config = runtime_config.update_config(
            db_session, oidc_enabled=True, apply_runtime=False
        )

        assert config.oidc_enabled is True
        assert "oidc_enabled" not in _overlay

    def test_update_config_uses_safe_integer_fallback_for_invalid_environment_value(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_BACKUP_RETENTION_DAYS", "not-an-integer")

        runtime_config.update_config(db_session, backup_retention_days=-1)

        assert _overlay["backup_retention_days"] == 30

    def test_unknown_environment_field_falls_back_to_empty_value(self) -> None:
        assert runtime_config._env_or_default("unknown_field") == ""  # noqa: SLF001

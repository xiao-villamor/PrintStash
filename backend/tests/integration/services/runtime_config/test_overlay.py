"""Runtime overlay projection and environment fallback integration tests.

These tests defend the boundary that turns persisted or environment provider
configuration into the live runtime overlay used by storage and authentication.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.config import _overlay, settings
from app.db.models import SystemConfig
from app.services import runtime_config


@pytest.fixture(autouse=True)
def _clean_overlay():
    saved = dict(_overlay)
    yield
    _overlay.clear()
    _overlay.update(saved)


class TestApplyOverlay:
    def test_markerless_nonempty_thumb_root_is_not_auto_enrolled(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        data_root = tmp_path / "files"
        thumb_root = tmp_path / "thumbs"
        data_root.mkdir()
        thumb_root.mkdir()
        (thumb_root / "wrong-installation.webp").write_bytes(b"not our thumbnail")
        _overlay.update(
            {
                "storage_backend": "local",
                "data_dir": data_root,
                "thumb_dir": thumb_root,
            }
        )

        runtime_config.ensure_storage_identity(db_session)
        result = runtime_config.enroll_legacy_local_roots(db_session)

        assert result == {"data": False, "thumb": False}
        assert not (data_root / ".printstash-storage-root.json").exists()
        assert not (thumb_root / ".printstash-storage-root.json").exists()
        assert (
            thumb_root / "wrong-installation.webp"
        ).read_bytes() == b"not our thumbnail"

    def test_apply_overlay_noop_when_no_config_row(self, db_session: Session) -> None:
        _overlay["storage_backend"] = "leftover"
        runtime_config.apply_overlay(db_session)

        assert _overlay.get("storage_backend") == "leftover"

    def test_apply_overlay_copies_all_persisted_fields(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.data_dir = "/data/vault"
        config.thumb_dir = "/data/thumbs"
        config.storage_backend = "s3"
        config.s3_bucket = "my-bucket"
        config.s3_endpoint_url = "https://s3.example.test"
        config.s3_region = "us-east-1"
        config.s3_access_key = "AKIA_TEST"
        config.s3_secret_key = "secret-value"
        config.backup_retention_days = 45
        config.trash_retention_days = 10
        config.backup_s3_bucket = "backup-bucket"
        config.oidc_enabled = True
        config.oidc_issuer_url = "https://idp.example.test"
        config.oidc_client_id = "client-123"
        config.oidc_client_secret = "client-secret"
        config.oidc_allow_insecure_http = True
        config.makerworld_token = "mw-token-abc"
        db_session.add(config)
        db_session.commit()

        runtime_config.apply_overlay(db_session)

        assert _overlay["data_dir"] == Path("/data/vault")
        assert _overlay["thumb_dir"] == Path("/data/thumbs")
        assert _overlay["storage_backend"] == "s3"
        assert _overlay["s3_bucket"] == "my-bucket"
        assert _overlay["backup_retention_days"] == 45
        assert _overlay["trash_retention_days"] == 10
        assert _overlay["oidc_enabled"] is True
        assert _overlay["oidc_client_secret"] == "client-secret"
        assert _overlay["oidc_allow_insecure_http"] is True
        assert "makerworld_cookie" not in _overlay

    def test_apply_overlay_clears_stale_keys_not_in_config(
        self, db_session: Session
    ) -> None:
        _overlay["some_stale_key_from_a_prior_boot"] = "gone"
        runtime_config.get_or_create(db_session)

        runtime_config.apply_overlay(db_session)

        assert "some_stale_key_from_a_prior_boot" not in _overlay

    def test_apply_overlay_projects_a_persisted_local_provider(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        runtime_config.update_storage_provider(
            db_session,
            provider="local",
            raw_config={
                "provider": "local",
                "data_dir": str(tmp_path / "files"),
                "thumb_dir": str(tmp_path / "thumbs"),
                "root": "models",
            },
        )
        for key in (
            "storage_provider",
            "storage_provider_config",
            "storage_backend",
            "data_dir",
            "thumb_dir",
        ):
            _overlay.pop(key, None)

        runtime_config.apply_overlay(db_session)

        assert _overlay["storage_provider"] == "local"
        assert _overlay["storage_backend"] == "local"
        assert _overlay["data_dir"] == tmp_path / "files"
        assert _overlay["thumb_dir"] == tmp_path / "thumbs"

    def test_apply_overlay_projects_a_persisted_s3_provider(
        self, db_session: Session
    ) -> None:
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
        for key in (
            "storage_provider",
            "storage_provider_config",
            "storage_backend",
            "data_dir",
            "thumb_dir",
        ):
            _overlay.pop(key, None)

        runtime_config.apply_overlay(db_session)

        assert _overlay["storage_provider"] == "s3"
        assert _overlay["storage_backend"] == "s3"
        assert _overlay["s3_bucket"] == "models"
        assert _overlay["s3_endpoint_url"] == "https://s3.example.test"
        assert _overlay["s3_region"] == "us-east-1"
        assert _overlay["s3_access_key"] == "fake-access"
        assert _overlay["s3_secret_key"] == "fake-secret"

    def test_apply_overlay_projects_a_persisted_remote_provider(
        self, db_session: Session
    ) -> None:
        runtime_config.update_storage_provider(
            db_session,
            provider="webdav",
            raw_config={
                "provider": "webdav",
                "endpoint_url": "https://dav.example.test",
                "username": "fake-user",
                "password": "fake-password",
                "root": "models",
            },
        )
        for key in ("storage_provider", "storage_provider_config", "storage_backend"):
            _overlay.pop(key, None)

        runtime_config.apply_overlay(db_session)

        assert _overlay["storage_provider"] == "webdav"
        assert _overlay["storage_backend"] == "webdav"

    def test_apply_overlay_copies_model_thumbnail_width(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.model_thumbnail_width = 800
        db_session.add(config)
        db_session.commit()

        runtime_config.apply_overlay(db_session)

        assert _overlay["model_thumbnail_width"] == 800

    def test_apply_overlay_discards_an_invalid_stored_provider(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "local"
        config.storage_provider_config_json = '{"provider":"local"}'
        db_session.add(config)
        db_session.commit()
        _overlay["stale"] = "value"

        runtime_config.apply_overlay(db_session)

        assert "storage_provider" not in _overlay
        assert "stale" not in _overlay

    def test_apply_overlay_ignores_a_provider_without_persisted_json(
        self, db_session: Session
    ) -> None:
        config = runtime_config.get_or_create(db_session)
        config.storage_provider = "local"
        db_session.add(config)
        db_session.commit()

        runtime_config.apply_overlay(db_session)

        assert "storage_provider" not in _overlay

    def test_stored_provider_config_is_absent_without_provider_json(self) -> None:
        config = SystemConfig(storage_provider="local")

        assert runtime_config._stored_provider_config(config) is None  # noqa: SLF001


class TestApplyEnvironmentStorageProvider:
    def test_projects_a_valid_provider_from_environment(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for name in (
            "VAULT_DATA_DIR",
            "VAULT_THUMB_DIR",
            "VAULT_STORAGE_ROOT",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(settings._frozen, "storage_provider", "local")  # noqa: SLF001
        monkeypatch.setattr(  # noqa: SLF001
            settings._frozen,
            "storage_provider_config",
            '{"provider":"local","data_dir":"%s","thumb_dir":"%s","root":"models"}'
            % (tmp_path / "files", tmp_path / "thumbs"),
        )
        monkeypatch.setattr(settings._frozen, "storage_provider_secrets", "{}")  # noqa: SLF001
        for key in (
            "storage_provider",
            "storage_provider_config",
            "storage_backend",
            "data_dir",
            "thumb_dir",
        ):
            _overlay.pop(key, None)

        runtime_config.apply_environment_storage_provider(db_session)

        assert _overlay["storage_provider"] == "local"
        assert _overlay["data_dir"] == tmp_path / "files"

    def test_rejects_generic_json_when_typed_provider_fields_are_present(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_DATA_DIR", "/tmp/typed-files")
        monkeypatch.setattr(settings._frozen, "storage_provider", "local")  # noqa: SLF001
        monkeypatch.setattr(  # noqa: SLF001
            settings._frozen,
            "storage_provider_config",
            '{"provider":"local","data_dir":"/tmp/json-files","thumb_dir":"/tmp/json-thumbs"}',
        )
        monkeypatch.setattr(settings._frozen, "storage_provider_secrets", "{}")  # noqa: SLF001

        runtime_config.apply_environment_storage_provider(db_session)

        assert _overlay["storage_provider_error"] == (
            "storage_provider_configuration_conflict"
        )

    def test_ignores_a_mismatched_environment_provider(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings._frozen, "storage_provider", "s3")  # noqa: SLF001
        monkeypatch.setattr(  # noqa: SLF001
            settings._frozen,
            "storage_provider_config",
            '{"provider":"local","data_dir":"/tmp/files","thumb_dir":"/tmp/thumbs"}',
        )
        monkeypatch.setattr(settings._frozen, "storage_provider_secrets", "{}")  # noqa: SLF001
        for key in (
            "storage_provider",
            "storage_provider_config",
            "storage_backend",
            "data_dir",
            "thumb_dir",
        ):
            _overlay.pop(key, None)

        runtime_config.apply_environment_storage_provider(db_session)

        assert "storage_provider" not in _overlay


class TestActivateConfig:
    def test_activate_config_applies_runtime_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = SystemConfig(storage_backend="local", model_thumbnail_width=800)
        monkeypatch.setattr(runtime_config, "ensure_dirs", lambda: None)
        _overlay.pop("storage_backend", None)
        _overlay.pop("model_thumbnail_width", None)

        runtime_config.activate_config(config)

        assert _overlay["storage_backend"] == "local"
        assert _overlay["model_thumbnail_width"] == 800

    def test_activate_config_creates_directories(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = SystemConfig()
        ensured = False

        def ensure_dirs() -> None:
            nonlocal ensured
            ensured = True

        monkeypatch.setattr(runtime_config, "ensure_dirs", ensure_dirs)

        runtime_config.activate_config(config)

        assert ensured is True


class TestJsonObject:
    def test_json_object_returns_an_empty_mapping_for_invalid_json(self) -> None:
        assert runtime_config._json_object("not-json") == {}  # noqa: SLF001

    def test_json_object_returns_an_empty_mapping_for_non_object_json(self) -> None:
        assert runtime_config._json_object("[]") == {}  # noqa: SLF001

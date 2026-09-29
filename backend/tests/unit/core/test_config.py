"""Validation contracts for environment-backed settings: numbers and paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import DATA_ROOT_LAYOUT, ConfigResolver, FrozenSettings, _overlay

DATA_ROOT = Path("/srv/printstash")
POSTGRES_URL = "postgresql+psycopg://printstash:secret@postgres:5432/printstash"


class TestSettings:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_upload_mb", 0),
            ("mesh_memory_budget_fraction", -0.01),
            ("mesh_memory_budget_fraction", 1.01),
            ("mesh_render_face_chunk_size", 0),
            ("mesh_step_timeout_seconds", 0),
            ("mesh_worker_timeout_seconds", 9),
            ("mesh_worker_timeout_seconds", 3601),
            ("mesh_stream_timeout_seconds", 0),
            ("mesh_stream_timeout_seconds", 46),
            ("max_archive_entries", 0),
            ("backup_retention_days", -1),
            ("trash_retention_days", -1),
            ("slow_request_ms", 0),
        ],
    )
    def test_numeric_settings_reject_impossible_values(
        self, field: str, value: int | float
    ) -> None:
        with pytest.raises(ValidationError):
            FrozenSettings(_env_file=None, **{field: value})

    def test_never_renders_the_provider_client_credentials(self) -> None:
        configured = FrozenSettings(
            _env_file=None, mmf_client_id="client-id", mmf_client_secret="client-secret"
        )

        rendered = repr(configured)

        # A settings object is repr'd into logs, error pages and crash reports.
        # Both halves of an OAuth client credential are secret: the id alone
        # identifies the deployment's app registration.
        assert "client-id" not in rendered
        assert "client-secret" not in rendered

    def test_slow_request_threshold_defaults_to_one_second(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VAULT_SLOW_REQUEST_MS", raising=False)

        assert FrozenSettings(_env_file=None).slow_request_ms == 1000

    def test_slow_request_threshold_reads_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_SLOW_REQUEST_MS", "250")

        assert FrozenSettings(_env_file=None).slow_request_ms == 250

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("mesh_memory_budget_fraction", 0),
            ("mesh_max_load_mb", 0),
            ("max_render_jobs", 0),
            ("bambu_external_capture_max_mb", 0),
            ("backup_retention_days", 0),
            ("trash_retention_days", 0),
        ],
    )
    def test_documented_zero_sentinels_remain_valid(
        self, field: str, value: int | float
    ) -> None:
        configured = FrozenSettings(_env_file=None, **{field: value})
        assert getattr(configured, field) == value

    def test_archive_entry_limit_cannot_exceed_total_limit(self) -> None:
        with pytest.raises(ValidationError, match="max_archive_entry_mb"):
            FrozenSettings(
                _env_file=None,
                max_archive_entry_mb=100,
                max_archive_uncompressed_mb=99,
            )


class TestDefaultUnderDataRoot:
    """One volume holds every app path, so one variable must place them all.

    A path that silently kept its own default would land outside the mounted
    volume and vanish with the container, and staging outside the library's
    mount turns every hard-linked import back into a full copy.
    """

    def test_defaults_the_data_root_to_the_container_volume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VAULT_DATA_ROOT")

        configured = FrozenSettings(_env_file=None)

        assert configured.data_root == Path("/data")

    @pytest.mark.parametrize(
        ("field", "child"),
        sorted(DATA_ROOT_LAYOUT.items()),
        ids=sorted(DATA_ROOT_LAYOUT),
    )
    def test_derives_each_app_path_under_the_data_root(
        self, field: str, child: str
    ) -> None:
        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert getattr(configured, field) == DATA_ROOT / child

    def test_derives_the_sqlite_database_under_the_data_root(self) -> None:
        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert configured.db_url == "sqlite:////srv/printstash/db/printstash.sqlite"

    def test_derives_a_relative_database_from_a_relative_root(self) -> None:
        configured = FrozenSettings(_env_file=None, data_root=Path("_data"))

        assert configured.db_url == "sqlite:///_data/db/printstash.sqlite"

    @pytest.mark.parametrize("field", sorted(DATA_ROOT_LAYOUT))
    def test_keeps_an_explicit_directory_override(
        self, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        monkeypatch.setenv(f"VAULT_{field.upper()}", "/mnt/hdd/moved")

        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert getattr(configured, field) == Path("/mnt/hdd/moved")

    def test_moves_only_the_overridden_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_DATA_DIR", "/mnt/hdd/files")

        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert configured.staging_dir == DATA_ROOT / "staging"

    @pytest.mark.parametrize("field", sorted(DATA_ROOT_LAYOUT))
    def test_treats_an_empty_override_as_the_layout_default(
        self, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        # An unset `${VAR:-}` in Compose renders as an empty string. Taken
        # literally it would be Path(""), the process's working directory.
        monkeypatch.setenv(f"VAULT_{field.upper()}", "")

        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert getattr(configured, field) == DATA_ROOT / DATA_ROOT_LAYOUT[field]

    def test_keeps_an_explicit_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_DB_URL", POSTGRES_URL)

        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert configured.db_url == POSTGRES_URL

    def test_treats_an_empty_database_url_as_the_layout_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_DB_URL", "")

        configured = FrozenSettings(_env_file=None, data_root=DATA_ROOT)

        assert configured.db_url == "sqlite:////srv/printstash/db/printstash.sqlite"


class TestConfigResolver:
    def test_frozen_ignores_a_runtime_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resolver = ConfigResolver(FrozenSettings(_env_file=None, data_root=DATA_ROOT))
        monkeypatch.setitem(_overlay, "data_dir", Path("/mnt/runtime/files"))

        assert resolver.frozen.data_dir == DATA_ROOT / "files"

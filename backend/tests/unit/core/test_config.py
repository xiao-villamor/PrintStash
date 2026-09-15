"""Validation contracts for environment-backed numeric settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import FrozenSettings


class TestSettings:
    @pytest.mark.parametrize("workers", [0, 1, 2, 32], ids=str)
    def test_accepts_import_worker_limits(self, workers):
        assert (
            FrozenSettings(_env_file=None, import_workers=workers).import_workers
            == workers
        )

    @pytest.mark.parametrize("workers", [-1, 33], ids=str)
    def test_rejects_invalid_import_worker_limits(self, workers):
        with pytest.raises(ValidationError, match="import_workers"):
            FrozenSettings(_env_file=None, import_workers=workers)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_upload_mb", 0),
            ("mesh_memory_budget_fraction", -0.01),
            ("mesh_memory_budget_fraction", 1.01),
            ("mesh_render_face_chunk_size", 0),
            ("mesh_step_timeout_seconds", 0),
            ("mesh_stream_timeout_seconds", 0),
            ("mesh_stream_timeout_seconds", 46),
            ("max_archive_entries", 0),
            ("backup_retention_days", -1),
            ("trash_retention_days", -1),
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


@pytest.mark.parametrize("name", ["mesh_rasterizer", "mesh_loader", "mesh_geometry"])
def test_removed_engine_settings_cannot_select_python(name):
    settings = FrozenSettings(_env_file=None, **{name: "python"})
    assert name not in FrozenSettings.model_fields
    assert not hasattr(settings, name)

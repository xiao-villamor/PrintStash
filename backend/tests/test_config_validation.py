"""Validation contracts for environment-backed numeric settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import FrozenSettings


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
    field: str, value: int | float
) -> None:
    with pytest.raises(ValidationError):
        FrozenSettings(_env_file=None, **{field: value})


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
def test_documented_zero_sentinels_remain_valid(field: str, value: int | float) -> None:
    configured = FrozenSettings(_env_file=None, **{field: value})
    assert getattr(configured, field) == value


def test_archive_entry_limit_cannot_exceed_total_limit() -> None:
    with pytest.raises(ValidationError, match="max_archive_entry_mb"):
        FrozenSettings(
            _env_file=None,
            max_archive_entry_mb=100,
            max_archive_uncompressed_mb=99,
        )

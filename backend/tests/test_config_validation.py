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
        ("max_archive_entries", 0),
        ("browser_fetch_timeout_seconds", 0),
        ("backup_retention_days", -1),
        ("trash_retention_days", -1),
    ],
)
def test_numeric_settings_reject_impossible_values(field: str, value: int | float) -> None:
    with pytest.raises(ValidationError):
        FrozenSettings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mesh_memory_budget_fraction", 0),
        ("mesh_max_load_mb", 0),
        ("max_render_jobs", 0),
        ("s3_lifecycle_expiration_days", 0),
        ("s3_lifecycle_transition_days", 0),
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


def test_s3_transition_must_precede_expiration_when_both_are_enabled() -> None:
    with pytest.raises(ValidationError, match="s3_lifecycle_transition_days"):
        FrozenSettings(
            _env_file=None,
            s3_lifecycle_transition_days=30,
            s3_lifecycle_expiration_days=30,
        )

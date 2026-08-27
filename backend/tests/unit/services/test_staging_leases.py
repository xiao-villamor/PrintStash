"""Capture uploads always spool beneath the process-local staging root.

Remote object keys must never be mistaken for local paths while upload bytes
are still being validated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import _overlay
from app.services.staging_leases import capture_slot_staging_path


class TestCaptureSlotStagingPath:
    def test_is_deterministic_and_separate_from_remote_object_keys(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        staging_root = tmp_path / "staging"
        monkeypatch.setitem(_overlay, "staging_dir", staging_root)
        monkeypatch.setitem(_overlay, "storage_backend", "s3")

        first = capture_slot_staging_path("slot-1")
        second = capture_slot_staging_path("slot-1")

        assert first == staging_root / "_incoming" / "capture-slots" / "slot-1.upload"
        assert second == first

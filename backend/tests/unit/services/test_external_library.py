"""Pure progress-coalescing behaviour for external-library scans."""

import pytest

from app.core.config import _overlay
from app.services import external_library
from app.services.external_library import _ScanProgressCoalescer


class TestScanProgressCoalescer:
    def test_empty_scan_flushes_only_after_work_appears(self) -> None:
        progress = _ScanProgressCoalescer(total=0, last_flush_at=10.0)

        assert progress.should_flush(0, now=10.0) is False
        assert progress.should_flush(1, now=10.0) is True

    def test_percentage_step_triggers_a_bounded_progress_write(self) -> None:
        progress = _ScanProgressCoalescer(total=100, last_flush_at=10.0)

        assert progress.should_flush(0, now=10.0) is False
        assert progress.should_flush(1, now=10.0) is True
        assert progress.last_percent == 1

    def test_elapsed_interval_triggers_a_bounded_progress_write(self) -> None:
        progress = _ScanProgressCoalescer(total=100, last_flush_at=10.0)

        assert progress.should_flush(1, now=11.0) is True
        assert progress.last_flush_at == 11.0

    def test_final_item_always_flushes_progress(self) -> None:
        progress = _ScanProgressCoalescer(total=3, last_flush_at=10.0)

        assert progress.should_flush(3, now=10.0) is True
        assert progress.last_percent == 100


class TestInstallationIdentity:
    def test_invalid_identity_is_never_used_for_root_binding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "storage_identity", "not-a-valid-identity")

        assert external_library._installation_identity() == ""

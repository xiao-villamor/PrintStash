"""Foreground priority is a local scheduling hint with balanced admission."""

import pytest

from app.runtime import maintenance


class TestForegroundMutations:
    def test_rejects_mismatched_foreground_release(self):
        assert maintenance.begin_mutating_operation()
        try:
            with pytest.raises(RuntimeError, match="unbalanced_mutating_operation"):
                maintenance.end_mutating_operation(foreground=True)
            assert not maintenance.foreground_mutations_pending()
        finally:
            maintenance.end_mutating_operation()

    def test_tracks_nested_foreground_writes(self):
        assert maintenance.begin_mutating_operation(foreground=True)
        try:
            assert maintenance.begin_mutating_operation(foreground=True)
            try:
                assert maintenance.foreground_mutations_pending()
            finally:
                maintenance.end_mutating_operation(foreground=True)
            assert maintenance.foreground_mutations_pending()
        finally:
            maintenance.end_mutating_operation(foreground=True)
        assert not maintenance.foreground_mutations_pending()

    def test_excludes_background_work(self):
        assert maintenance.begin_mutating_operation()
        try:
            assert not maintenance.foreground_mutations_pending()
        finally:
            maintenance.end_mutating_operation()

    def test_releases_priority_after_failed_admission(self, monkeypatch):
        def reject():
            raise RuntimeError("observer refused")

        monkeypatch.setattr(maintenance, "_mutation_observer", reject)
        with pytest.raises(RuntimeError, match="observer refused"):
            maintenance.begin_mutating_operation(foreground=True)
        assert not maintenance.foreground_mutations_pending()
        maintenance.begin_restore_maintenance()
        maintenance.end_restore_maintenance()

    def test_refuses_foreground_writes_during_restore(self):
        maintenance.hold_restore_maintenance()
        try:
            assert not maintenance.begin_mutating_operation(foreground=True)
            assert not maintenance.foreground_mutations_pending()
        finally:
            maintenance.end_restore_maintenance()

"""Failed attempts retain costs; private teardown requires physical quiescence."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import create_engine

from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from scripts.benchmark_cleanup import CleanupObservation
from scripts.benchmark_ingestion import measure_ingestion, observe_teardown
from scripts.benchmark_pipeline_contracts import InputIdentity, SampleOutcome


class TestMeasureIngestion:
    def test_retains_source_probe_failure(self, client, tmp_path):
        # An intentionally incomplete schema belongs to this test alone. Dropping
        # the shared files table also removed its browse triggers for later tests.
        engine = create_engine(f"sqlite:///{tmp_path / 'missing-source.sqlite'}")
        previous = get_session_factory()
        override_session_factory(SQLiteSessionFactory(engine))
        try:
            result = measure_ingestion(
                client,
                Path("unread-source.stl"),
                InputIdentity("unread-source.stl", "a" * 64, 684, 1),
                deadline_seconds=1,
            )
        finally:
            override_session_factory(previous)
            engine.dispose()
        assert result.outcome == SampleOutcome.FAILED
        assert "OperationalError" in result.reason
        assert "no such table: files" in result.reason
        assert 0 < result.source_probe_ms <= result.elapsed_ms
        assert result.source_preexisting is None
        assert result.artifact_reused is None
        assert result.accepted_ms is None
        assert result.file_id is None


class TestObserveTeardown:
    def test_preserves_cleanup_failure_after_owners_drain(self, db_session):
        from app.modules.storage.storage_backend import generations
        from app.runtime import maintenance

        prior = CleanupObservation(1, False, 1, True, (), ("cleanup_failed",))
        maintenance.hold_restore_maintenance()
        try:
            result = observe_teardown(prior, epoch=generations.current_epoch())
            assert result.quiescent is False
            assert result.active_mutations == 0
            assert result.active_readers is False
            assert result.errors == ("cleanup_failed",)
            assert maintenance.begin_mutating_operation() is False
        finally:
            maintenance.end_restore_maintenance()

    def test_rejects_active_mutations_after_teardown(self, db_session):
        from app.modules.storage.storage_backend import generations
        from app.runtime import maintenance

        prior = CleanupObservation(1, True, 0, False, (), ())
        assert maintenance.begin_mutating_operation() is True
        maintenance.hold_restore_maintenance()
        try:
            result = observe_teardown(prior, epoch=generations.current_epoch())
            assert result.quiescent is False
            assert result.active_mutations == 1
            assert result.active_readers is False
            assert result.errors == ("private_work_active_after_teardown",)
            assert maintenance.begin_mutating_operation() is False
        finally:
            maintenance.end_mutating_operation()
            maintenance.end_restore_maintenance()

    def test_rejects_active_readers_after_teardown(self, db_session):
        from app.modules.storage.storage_backend import generations
        from app.runtime import maintenance

        prior = CleanupObservation(1, True, 0, False, (), ())
        lease = generations.pin()
        maintenance.hold_restore_maintenance()
        try:
            result = observe_teardown(prior, epoch=lease.epoch)
            assert result.quiescent is False
            assert result.active_mutations == 0
            assert result.active_readers is True
            assert result.errors == ("private_work_active_after_teardown",)
            assert maintenance.begin_mutating_operation() is False
        finally:
            lease.close()
            maintenance.end_restore_maintenance()

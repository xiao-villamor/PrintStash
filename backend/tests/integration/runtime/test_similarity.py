"""Similarity units yield to maintenance and retain storage through cancellation."""

import asyncio
import logging
import threading

import pytest

from app.core.errors import ErrorKind, OperationError
from app.modules.similarity import configuration, runs
from app.runtime import maintenance, similarity
from app.runtime.work_wakeup import LocalWorkWakeup


class TestSimilarityRuntime:
    def test_idle_poll_does_not_retain_storage(self, db_session, monkeypatch):
        from app.modules.similarity import processing

        read_settings = processing.read_settings

        def read_while_cleanup_is_available(session):
            admitted = maintenance.begin_destructive_operation()
            if admitted:
                maintenance.end_destructive_operation()
            assert admitted, "An idle settings poll must not retain stored objects"
            return read_settings(session)

        monkeypatch.setattr(
            processing, "read_settings", read_while_cleanup_is_available
        )

        assert similarity.process_one() is False

    def test_defers_work_during_restore(self):
        maintenance.hold_restore_maintenance()
        try:
            assert similarity.process_one() is False
        finally:
            maintenance.end_restore_maintenance()

    def test_defers_work_during_storage_cleanup(self, db_session, make_user):
        actor = make_user(superuser=True)
        configuration.update_settings(db_session, actor, {"enabled": True})
        runs.start(db_session, actor)
        assert maintenance.begin_destructive_operation()
        try:
            assert similarity.process_one() is False
        finally:
            maintenance.end_destructive_operation()
        # A deferred unit must release its mutation registration.
        maintenance.begin_restore_maintenance()
        maintenance.end_restore_maintenance()

    def test_retains_storage_while_worker_executes(
        self, db_session, monkeypatch, make_user
    ):
        actor = make_user(superuser=True)
        configuration.update_settings(db_session, actor, {"enabled": True})
        runs.start(db_session, actor)

        def check_retention(_worker, *_args):
            assert maintenance.begin_destructive_operation() is False
            return True

        monkeypatch.setattr(
            similarity.SimilarityProcessor, "_fingerprint", check_retention
        )
        assert similarity.process_one() is True
        assert maintenance.begin_destructive_operation()
        maintenance.end_destructive_operation()

    def test_releases_admission_when_work_fails(self, db_session, monkeypatch):
        def fail(_worker):
            raise OperationError("analysis_unavailable", kind=ErrorKind.CONFLICT)

        monkeypatch.setattr(similarity.SimilarityProcessor, "work_one", fail)
        with pytest.raises(OperationError, match="analysis_unavailable"):
            similarity.process_one()
        maintenance.begin_restore_maintenance()
        maintenance.end_restore_maintenance()
        assert maintenance.begin_destructive_operation()
        maintenance.end_destructive_operation()

    @pytest.mark.asyncio
    async def test_cancellation_waits_for_admitted_unit(self, db_session, monkeypatch):
        entered, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def bounded(_worker):
            entered.set()
            assert release.wait(timeout=5)
            finished.set()
            return True

        monkeypatch.setattr(similarity.SimilarityProcessor, "work_one", bounded)
        task = asyncio.create_task(similarity.run_similarity(LocalWorkWakeup()))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert finished.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        maintenance.begin_restore_maintenance()
        maintenance.end_restore_maintenance()

    @pytest.mark.asyncio
    async def test_reports_worker_failure_without_paths(
        self, db_session, monkeypatch, caplog
    ):
        def fail():
            raise ValueError("private-path source-token")

        class StopWakeup:
            async def wait(self):
                raise asyncio.CancelledError

        monkeypatch.setattr(similarity, "process_one", fail)
        with caplog.at_level(logging.WARNING, logger=similarity.logger.name):
            with pytest.raises(asyncio.CancelledError):
                await similarity.run_similarity(StopWakeup())
        assert "inspect its run status" in caplog.text
        assert "private-path" not in caplog.text
        assert "source-token" not in caplog.text

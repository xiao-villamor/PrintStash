"""Warm-up shutdown cancels the native context before the runtime drains."""

import asyncio
import threading

import pytest

from app.runtime import maintenance, model_warmup


class TestModelWarmupRuntime:
    def test_defers_model_loading_during_restore(self, db_session):
        processor = model_warmup.ModelWarmup(model_warmup.get_session_factory())
        maintenance.hold_restore_maintenance()
        try:
            assert not model_warmup.process_one(processor)
        finally:
            maintenance.end_restore_maintenance()

    @pytest.mark.asyncio
    async def test_cancels_loading_before_shutdown_completes(self, monkeypatch):
        entered, finished = threading.Event(), threading.Event()

        def bounded(processor):
            entered.set()
            assert processor.stopped.wait(timeout=5)
            finished.set()
            return False

        monkeypatch.setattr(model_warmup, "process_one", bounded)
        task = asyncio.create_task(model_warmup.run_model_warmup())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert finished.is_set()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

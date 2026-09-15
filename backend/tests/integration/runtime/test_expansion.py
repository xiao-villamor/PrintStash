"""Sparse work respects restore admission and finishes its lease before shutdown."""

import asyncio
import threading

import pytest
from sqlmodel import select

from app.db.models import SearchExpansion
from app.runtime import expansion, maintenance


class TestExpansionRuntime:
    def test_defers_sparse_work_during_restore(self, db_session):
        maintenance.hold_restore_maintenance()
        try:
            assert expansion.process_one() is False
            assert db_session.exec(select(SearchExpansion)).all() == []
        finally:
            maintenance.end_restore_maintenance()

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_the_sparse_lease(self, monkeypatch):
        entered, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def bounded():
            entered.set()
            assert release.wait(timeout=5)
            finished.set()
            return True

        monkeypatch.setattr(expansion, "process_one", bounded)
        task = asyncio.create_task(expansion.run_expansion())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert finished.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

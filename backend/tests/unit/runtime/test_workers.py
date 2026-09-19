"""Runtime scheduling recovers infrastructure failures and drains shutdown."""

import asyncio

import pytest

from app.runtime.workers import run_async_worker


class TestWorkersContract:
    @pytest.mark.asyncio
    async def test_shutdown_survives_a_failing_asynchronous_unit(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def operation():
            started.set()
            await release.wait()
            raise RuntimeError("asynchronous unit failed during shutdown")

        worker = asyncio.create_task(run_async_worker(operation))
        await started.wait()
        worker.cancel()
        await asyncio.sleep(0)
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=2)

    @pytest.mark.asyncio
    async def test_worker_recovers_after_an_infrastructure_failure(self):
        recovered = asyncio.Event()
        attempts = 0

        async def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("database unavailable")
            recovered.set()
            return False

        worker = asyncio.create_task(run_async_worker(operation))
        await asyncio.wait_for(recovered.wait(), timeout=3)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_worker_drains_the_active_unit_before_shutdown(self):
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()

        async def operation():
            started.set()
            await release.wait()
            completed.set()
            return True

        worker = asyncio.create_task(run_async_worker(operation))
        await started.wait()
        worker.cancel()
        await asyncio.sleep(0)
        assert not worker.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert completed.is_set()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("owner", ["shared", "search"])
    async def test_shutdown_survives_an_inflight_unit_failure(self, owner):
        import threading

        from app.runtime.search import _run_unit
        from app.runtime.workers import run_unit

        started = threading.Event()
        release = threading.Event()

        def failing_unit():
            started.set()
            assert release.wait(timeout=5)
            raise RuntimeError("unit failed during shutdown")

        task = asyncio.create_task(
            (run_unit if owner == "shared" else _run_unit)(failing_unit)
        )
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

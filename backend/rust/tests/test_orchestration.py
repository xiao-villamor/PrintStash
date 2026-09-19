"""Native workers and admission remain bounded through failures and shutdown."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import printstash_mesh_native as native
import pytest


@pytest.fixture
def pool():
    executor = native.NativeExecutor(2)
    yield executor
    executor.shutdown(wait=True, cancel_futures=True)


class TestNativeExecutor:
    def test_tasks_run_concurrently(self, pool):
        ready = Event()
        first = pool.submit(ready.wait, 5)
        second = pool.submit(ready.set)

        assert first.result(timeout=5) is True
        assert second.result(timeout=5) is None

    def test_failure_does_not_stop_other_tasks(self, pool):
        failed = pool.submit(int, "invalid")
        other = pool.submit(int, "42")

        with pytest.raises(RuntimeError, match="invalid literal"):
            failed.result(timeout=5)
        assert other.result(timeout=5) == 42

    def test_timeout_keeps_task_alive(self, pool):
        ready = Event()
        task = pool.submit(ready.wait, 5)
        try:
            with pytest.raises(TimeoutError, match="pending"):
                task.result(timeout=0)
        finally:
            ready.set()
        assert task.result(timeout=5) is True

    def test_queue_is_bounded(self):
        pool = native.NativeExecutor(1)
        ready = Event()
        try:
            first = pool.submit(ready.wait, 5)
            second = pool.submit(int, "2")
            with pytest.raises(RuntimeError, match="queue is full"):
                pool.submit(int, "3")
        finally:
            ready.set()
            pool.shutdown()
        assert first.result() is True
        assert second.result() == 2

    def test_shutdown_cancels_queued_work(self):
        pool = native.NativeExecutor(1)
        started, release = Event(), Event()

        def work():
            started.set()
            return release.wait(5)

        first = pool.submit(work)
        assert started.wait(5)
        second = pool.submit(int, "2")
        pool.shutdown(wait=False, cancel_futures=True)
        release.set()
        pool.shutdown()

        assert first.result() is True
        with pytest.raises(RuntimeError, match="cancelled"):
            second.result()

    def test_worker_cannot_wait_for_its_own_pool(self, pool):
        task = pool.submit(pool.shutdown)

        with pytest.raises(RuntimeError, match="own pool"):
            task.result(timeout=5)

    def test_shutdown_rejects_submission(self, pool):
        pool.shutdown()

        with pytest.raises(RuntimeError, match="cannot schedule"):
            pool.submit(int, "2")

    @pytest.mark.parametrize("workers", [0, 33])
    def test_invalid_worker_count_is_rejected(self, workers):
        with pytest.raises(ValueError, match="worker count"):
            native.NativeExecutor(workers)

    def test_noncallable_is_rejected(self, pool):
        with pytest.raises(ValueError, match="callable"):
            pool.submit(42)

    @pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), 86401])
    def test_invalid_timeout_is_rejected(self, pool, timeout):
        task = pool.submit(int, "2")

        with pytest.raises(ValueError, match="timeout"):
            task.result(timeout=timeout)


class TestNativeBudget:
    def test_capacity_waits_for_release(self):
        gate = native.NativeBudget()
        lease = gate.acquire(60, 100, 2)
        assert gate.acquire(50, 100, 2, False) is None
        with ThreadPoolExecutor(1) as executor:
            waiting = executor.submit(gate.acquire, 100, 100, 2)
            lease.release()
            acquired = waiting.result(timeout=5)
        assert gate.used == 100
        acquired.release()
        assert gate.used == 0

    def test_jobs_limit_is_shared(self):
        gate = native.NativeBudget()
        lease = gate.acquire(1, 100, 1)

        assert gate.acquire(1, 100, 1, False) is None
        lease.release()
        assert gate.jobs == 0

    def test_release_is_idempotent(self):
        gate = native.NativeBudget()
        lease = gate.acquire(40, 100, 2)
        lease.release()
        lease.release()

        assert (gate.used, gate.jobs) == (0, 0)

    def test_dropped_lease_releases_memory(self):
        gate = native.NativeBudget()
        lease = gate.acquire(40, 100, 2)
        del lease

        assert (gate.used, gate.jobs) == (0, 0)

    @pytest.mark.parametrize("capacity,jobs", [(0, 1), (1, 0)])
    def test_invalid_capacity_is_rejected(self, capacity, jobs):
        with pytest.raises(ValueError, match="capacity"):
            native.NativeBudget().acquire(1, capacity, jobs)

"""Real child lifetimes exercise bounded resident models and admission races."""

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext

from app.modules.inference.worker_pool import WorkerPool


@pytest.fixture
def workers():
    value = WorkerPool(idle_seconds=1)
    yield value
    value.close()


@pytest.fixture
def spawn():
    env = dict(os.environ)
    # The disposable child imports no application code and several tests kill it
    # deliberately. Instrumenting it cannot add app coverage and leaves an invalid
    # SQLite coverage shard after SIGKILL.
    env.pop("COVERAGE_PROCESS_CONFIG", None)

    def start():
        return subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )

    return start


class TestWorkerPool:
    def test_reuses_a_loaded_worker(self, workers, spawn, tmp_path):
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as first:
            assert first.poll() is None
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as second:
            assert second.pid == first.pid

    def test_evicts_the_least_recent_idle_worker(self, workers, spawn, tmp_path):
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as first:
            pass
        with workers.acquire(
            (2,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as second:
            pass
        with workers.acquire((3,), tmp_path, spawn, InferenceContext.bounded(5)):
            assert first.poll() is not None
            assert second.poll() is None

    def test_defers_background_work_while_busy(self, workers, spawn, tmp_path):
        with workers.acquire((1,), tmp_path, spawn, InferenceContext.bounded(5)):
            with pytest.raises(EmbeddingError, match="compute_busy"):
                with workers.acquire(
                    (1,),
                    tmp_path,
                    spawn,
                    InferenceContext.bounded(5, priority="background"),
                ):
                    pytest.fail("busy worker was admitted")

    def test_prioritizes_waiting_queries(self, workers, spawn, tmp_path):
        queued = threading.Event()

        def query():
            queued.set()
            with workers.acquire(
                (1,), tmp_path, spawn, InferenceContext.bounded(5)
            ) as process:
                return process.pid

        with ThreadPoolExecutor(1) as executor:
            with workers.acquire(
                (1,), tmp_path, spawn, InferenceContext.bounded(5)
            ) as first:
                future = executor.submit(query)
                assert queued.wait(1)
                until = time.monotonic() + 2
                while (
                    workers._workers[(1,)].waiting_queries != 1
                    and time.monotonic() < until
                ):
                    time.sleep(0.005)
                assert workers._workers[(1,)].waiting_queries == 1
                with pytest.raises(EmbeddingError, match="compute_busy"):
                    with workers.acquire(
                        (1,),
                        tmp_path,
                        spawn,
                        InferenceContext.bounded(5, priority="background"),
                    ):
                        pytest.fail("background work jumped the queue")
            assert future.result(timeout=3) == first.pid

    @pytest.mark.parametrize("crash", [True, False])
    def test_evicts_a_failed_worker(self, workers, spawn, tmp_path, crash):
        with pytest.raises(EmbeddingError, match="inference_failed"):
            with workers.acquire(
                (1,), tmp_path, spawn, InferenceContext.bounded(5)
            ) as first:
                if crash:
                    first.kill()
                    first.wait()
                raise EmbeddingError("embedding_inference_failed")
        assert first.poll() is not None
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as second:
            assert first.pid != second.pid

    def test_protects_a_busy_worker_from_pruning(self, workers, spawn, tmp_path):
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as process:
            assert not workers.discard_directory(tmp_path)
            assert process.poll() is None
        assert workers.discard_directory(tmp_path)
        assert process.poll() is not None

    def test_expires_idle_workers(self, workers, spawn, tmp_path):
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as process:
            pass
        workers._workers[(1,)].touched -= 2
        workers.prune_idle()
        assert process.poll() is not None

    def test_keeps_a_referenced_model_warm(self, workers, spawn, tmp_path):
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as first:
            pass
        workers._workers[(1,)].touched -= 2
        workers.prune_idle((tmp_path,))
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as second:
            assert second.pid == first.pid
        workers._workers[(1,)].touched -= 2
        workers.prune_idle()
        assert first.poll() is not None

    def test_cancels_worker_admission(self, workers, spawn, tmp_path):
        cancel = threading.Event()
        with workers.acquire((1,), tmp_path, spawn, InferenceContext.bounded(5)):
            cancel.set()
            with pytest.raises(EmbeddingError, match="inference_cancelled"):
                with workers.acquire(
                    (1,),
                    tmp_path,
                    spawn,
                    InferenceContext.bounded(5, cancelled=cancel.is_set),
                ):
                    pytest.fail("cancelled request was admitted")
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as process:
            assert process.poll() is None


class TestPendingWorkerSpawn:
    def test_simultaneous_same_model_acquires_publish_one_child(
        self, workers, spawn, tmp_path
    ):
        started = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        children = []

        def delayed_spawn():
            started.set()
            assert release.wait(3)
            process = spawn()
            children.append(process)
            return process

        def acquire(second=False):
            if second:
                second_started.set()
            with workers.acquire(
                (1,), tmp_path, delayed_spawn, InferenceContext.bounded(5)
            ) as process:
                return process.pid

        with ThreadPoolExecutor(2) as executor:
            first = executor.submit(acquire)
            try:
                assert started.wait(1)
                second = executor.submit(acquire, True)
                assert second_started.wait(1)
                release.set()
                assert first.result(timeout=3) == second.result(timeout=3)
                assert len(children) == 1
            finally:
                release.set()

    def test_pending_spawn_counts_toward_capacity(self, spawn, tmp_path):
        workers = WorkerPool(capacity=1)
        started = threading.Event()
        release = threading.Event()

        def delayed_spawn():
            started.set()
            assert release.wait(3)
            return spawn()

        def first():
            with workers.acquire(
                (1,), tmp_path, delayed_spawn, InferenceContext.bounded(5)
            ) as process:
                return process.pid

        def second():
            with workers.acquire(
                (2,),
                tmp_path,
                spawn,
                InferenceContext.bounded(5, priority="background"),
            ):
                pytest.fail("pending cold spawn did not occupy cache capacity")

        with ThreadPoolExecutor(2) as executor:
            first_result = executor.submit(first)
            try:
                assert started.wait(1)
                second_result = executor.submit(second)
                with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
                    second_result.result(timeout=0.5)
            finally:
                release.set()
                first_result.result(timeout=3)
                workers.close()

    def test_failed_spawn_releases_pending_model_intent(self, workers, spawn, tmp_path):
        def fail():
            raise OSError("model process could not start")

        with pytest.raises(OSError, match="model process could not start"):
            with workers.acquire((1,), tmp_path, fail, InferenceContext.bounded(5)):
                pytest.fail("failed process entered the worker cache")
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as process:
            assert process.poll() is None
            assert len(workers._workers) == 1

    def test_model_directory_cannot_be_discarded_during_cold_spawn(
        self, workers, spawn, tmp_path
    ):
        started = threading.Event()
        release = threading.Event()

        def delayed_spawn():
            started.set()
            assert release.wait(3)
            return spawn()

        def acquire():
            with workers.acquire(
                (1,), tmp_path, delayed_spawn, InferenceContext.bounded(5)
            ) as process:
                return process.pid

        with ThreadPoolExecutor(2) as executor:
            result = executor.submit(acquire)
            try:
                assert started.wait(1)
                discarded = executor.submit(workers.discard_directory, tmp_path)
                assert discarded.result(timeout=0.5) is False
            finally:
                release.set()
                result.result(timeout=3)

    def test_shutdown_rejects_late_cold_spawn_publication(
        self, workers, spawn, tmp_path
    ):
        started = threading.Event()
        release = threading.Event()
        entered = threading.Event()
        children = []

        def delayed_spawn():
            started.set()
            assert release.wait(3)
            process = spawn()
            children.append(process)
            return process

        def acquire():
            with workers.acquire(
                (1,), tmp_path, delayed_spawn, InferenceContext.bounded(5)
            ):
                entered.set()

        with ThreadPoolExecutor(2) as executor:
            result = executor.submit(acquire)
            try:
                assert started.wait(1)
                closed = executor.submit(workers.close)
                assert closed.result(timeout=0.5) is None
                release.set()
                assert isinstance(result.exception(timeout=3), EmbeddingError)
                assert not entered.is_set()
                assert children[0].poll() is not None
                assert not workers._workers
            finally:
                release.set()


@pytest.fixture
def retirement_cleanup():
    from app.modules.inference.worker_pool import Worker

    original_close = Worker.close
    children = []
    try:
        yield children
    finally:
        # A RED may deliberately demonstrate lost pool bookkeeping. Keep the
        # actual child cleanup independent of that broken ownership path.
        for worker in children:
            if worker.process.poll() is None:
                original_close(worker)


class TestFailedWorkerRetirement:
    def test_cleanup_attempts_every_child_before_preserving_the_first_error(
        self, workers, spawn, tmp_path, monkeypatch, retirement_cleanup
    ):
        from app.modules.inference.worker_pool import Worker

        with workers.acquire(
            (1,), tmp_path / "first", spawn, InferenceContext.bounded(5)
        ) as first:
            pass
        with workers.acquire(
            (2,), tmp_path / "second", spawn, InferenceContext.bounded(5)
        ) as second:
            pass
        retirement_cleanup.extend(workers._workers.values())
        original_close = Worker.close
        original_error = OSError("first child could not be reaped")
        attempted = []

        def close(worker):
            attempted.append(worker.process.pid)
            if worker.process is first and attempted.count(first.pid) == 1:
                raise original_error
            original_close(worker)

        monkeypatch.setattr(Worker, "close", close)
        try:
            with pytest.raises(OSError) as caught:
                workers.close()
            assert caught.value is original_error
            assert attempted == [first.pid, second.pid]
            assert second.poll() is not None
            assert first.poll() is None
            assert [worker.process for worker in workers._retiring.values()] == [first]
        finally:
            # The explicit shutdown retry must finish the retained cleanup intent.
            workers.close()
        assert first.poll() is not None
        assert not workers._retiring

    def test_failed_retirement_protects_directory_until_prune_retry(
        self, workers, spawn, tmp_path, monkeypatch, retirement_cleanup
    ):
        from app.modules.inference.worker_pool import Worker

        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as process:
            pass
        retirement_cleanup.extend(workers._workers.values())
        original_close = Worker.close
        failures = []

        def close(worker):
            if worker.process is process and not failures:
                failures.append(worker.process.pid)
                raise OSError("temporary reap failure")
            original_close(worker)

        monkeypatch.setattr(Worker, "close", close)
        with pytest.raises(OSError, match="temporary reap failure"):
            workers.close()
        assert workers.discard_directory(tmp_path) is False
        assert process.poll() is None
        workers.prune_idle()
        assert process.poll() is not None
        assert workers.discard_directory(tmp_path) is True
        assert not workers._retiring

    def test_failed_retirement_retains_capacity_until_shutdown_retry(
        self, spawn, tmp_path, monkeypatch, retirement_cleanup
    ):
        from app.modules.inference.worker_pool import Worker

        workers = WorkerPool(capacity=1)
        original_close = Worker.close
        failures = []
        try:
            with workers.acquire(
                (1,), tmp_path, spawn, InferenceContext.bounded(5)
            ) as process:
                pass

            retirement_cleanup.extend(workers._workers.values())

            def close(worker):
                if worker.process is process and not failures:
                    failures.append(worker.process.pid)
                    raise OSError("temporary wait failure")
                original_close(worker)

            monkeypatch.setattr(Worker, "close", close)
            with pytest.raises(OSError, match="temporary wait failure"):
                workers.close()

            def forbidden_spawn():
                pytest.fail("unreaped child capacity was reused")

            with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
                with workers.acquire(
                    (2,),
                    tmp_path,
                    forbidden_spawn,
                    InferenceContext.bounded(5, priority="background"),
                ):
                    pytest.fail("unreaped child capacity was reused")
            assert process.poll() is None
            workers.close()
            assert process.poll() is not None
            with workers.acquire(
                (2,), tmp_path, spawn, InferenceContext.bounded(5)
            ) as admitted:
                assert admitted.poll() is None
                assert admitted.pid != process.pid
        finally:
            workers.close()

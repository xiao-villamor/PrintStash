"""Real child lifetimes exercise bounded resident models and admission races."""

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
    return lambda: subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )


class TestSharedNativeBudget:
    def test_external_renderer_evicts_idle_encoders_under_one_memory_budget(
        self, workers, spawn, tmp_path
    ):
        context = InferenceContext.bounded(5)
        with workers.acquire(("encoder",), tmp_path, spawn, context) as encoder:
            pass
        renderer = spawn()
        try:
            sizes = {encoder.pid: 70, renderer.pid: 50}
            workers.enforce_memory_budget(renderer, 100, sizes.get)
            assert encoder.poll() is not None
            assert renderer.poll() is None
            with pytest.raises(EmbeddingError, match="embedding_worker_oom"):
                workers.enforce_memory_budget(renderer, 40, sizes.get)
        finally:
            renderer.kill()
            renderer.wait()
            renderer.stdin.close()
            renderer.stdout.close()


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

    def test_enforces_the_total_worker_memory_budget(self, workers, spawn, tmp_path):
        with workers.acquire(
            (1,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as first:
            pass
        with workers.acquire(
            (2,), tmp_path, spawn, InferenceContext.bounded(5)
        ) as second:
            workers.enforce_memory_budget(second, 100, lambda pid: 60)
            assert first.poll() is not None
            assert second.poll() is None
            with pytest.raises(EmbeddingError, match="worker_oom"):
                workers.enforce_memory_budget(second, 50, lambda pid: 60)

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

"""Tiny licensed ONNX models retain one bounded residency across warm queries."""

import json
import os
import selectors
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO

import pytest
from printstash_core.inference import EmbeddingInput
from printstash_core.inference.context import InferenceContext

from app.core.config import _overlay
from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.worker_pool import pool
from tests.factories.embeddings import local_embedding_assets
from tests.paths import BACKEND_DIR


def _readable(stream: int | BinaryIO, timeout: float) -> bool:
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        return bool(selector.select(timeout))


@pytest.fixture
def high_pipe():
    fcntl = pytest.importorskip(
        "fcntl", reason="High pipe descriptors require POSIX F_DUPFD"
    )
    resource = pytest.importorskip(
        "resource", reason="High pipe descriptors require POSIX RLIMIT_NOFILE"
    )
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    soft, hard = limits
    if hard != resource.RLIM_INFINITY and hard <= 1024:
        pytest.skip(
            "Host hard RLIMIT_NOFILE cannot allocate a descriptor above FD_SETSIZE"
        )
    raised = soft != resource.RLIM_INFINITY and soft <= 1024
    if raised:
        resource.setrlimit(resource.RLIMIT_NOFILE, (1025, hard))
    low_reader = reader = writer = None
    try:
        low_reader, writer = os.pipe()
        reader = fcntl.fcntl(low_reader, fcntl.F_DUPFD, 1024)
        assert reader >= 1024
        yield reader, writer
    finally:
        for descriptor in (low_reader, reader, writer):
            if descriptor is not None:
                os.close(descriptor)
        if raised:
            resource.setrlimit(resource.RLIMIT_NOFILE, limits)


class TestDescriptorReadiness:
    def test_observes_ready_bytes_above_fd_setsize(self, high_pipe):
        reader, writer = high_pipe
        os.write(writer, b"x")

        assert _readable(reader, 0)
        assert os.read(reader, 1) == b"x"

    def test_bounds_an_unready_high_descriptor(self, high_pipe):
        reader, _writer = high_pipe

        assert not _readable(reader, 0)


@pytest.fixture
def residency(tmp_path, monkeypatch):
    from app.runtime import inference_resources
    from app.runtime.native_admission import LocalResourcePool

    pool.close()
    directory = tmp_path / "residency"
    previous = inference_resources.bind_pool(LocalResourcePool(directory))
    monkeypatch.setitem(_overlay, "embedding_memory_budget_fraction", 0.25)
    monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
    monkeypatch.setitem(_overlay, "embedding_resident_workers", 1)
    monkeypatch.setitem(_overlay, "embedding_worker_memory_mb", 1024)
    try:
        yield directory
    finally:
        pool.close()
        inference_resources.bind_pool(previous)


@pytest.fixture
def resident_provider(tmp_path, residency):
    assets = local_embedding_assets(tmp_path / "assets")
    return LocalEmbeddingProvider(
        get_session_factory(), assets, "two-tower-contract", 1
    )


class TestInferenceResidency:
    def test_reuses_a_resident_onnx_worker_for_warm_queries(self, resident_provider):
        provider = resident_provider
        first = provider.embed((EmbeddingInput("text", text="red"),), provider.space)
        process = pool._workers[provider._worker_key()].process
        second = provider.embed((EmbeddingInput("text", text="red"),), provider.space)
        assert first == second == ((1.0, 0.0, 0.0),)
        assert provider.is_warm
        assert pool._workers[provider._worker_key()].process.pid == process.pid
        assert process.poll() is None

    @pytest.mark.parametrize("_linux", [True] if sys.platform == "linux" else [])
    def test_resident_child_enforces_its_physical_memory_quota(
        self, resident_provider, _linux
    ):
        from app.runtime import inference_resources

        provider = resident_provider
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1.0, 0.0, 0.0),)
        process = pool._workers[provider._worker_key()].process
        maximum = next(
            line
            for line in Path(f"/proc/{process.pid}/limits").read_text().splitlines()
            if line.startswith("Max address space")
        ).split()
        expected = min(1024 * 1024**2, inference_resources.capacity().bytes)
        assert expected > 0
        assert int(maximum[3]) == int(maximum[4]) == expected

    def test_residency_pressure_retires_a_protected_idle_model(
        self, resident_provider, tmp_path
    ):
        first = resident_provider
        assert first.embed((EmbeddingInput("text", text="red"),), first.space) == (
            (1.0, 0.0, 0.0),
        )
        process = pool._workers[first._worker_key()].process
        pool.prune_idle((first.directory,))
        second = LocalEmbeddingProvider(
            get_session_factory(),
            local_embedding_assets(tmp_path / "second-assets"),
            "two-tower-contract",
            1,
        )
        result = second.embed(
            (EmbeddingInput("text", text="blue"),),
            second.space,
            context=InferenceContext.bounded(10),
        )
        assert result == ((0.0, 0.0, 1.0),)
        assert second.is_warm
        assert process.wait(timeout=5) == 73
        assert not first.is_warm
        assert pool._workers[second._worker_key()].process.poll() is None

    @pytest.mark.parametrize("_linux", [True] if sys.platform == "linux" else [])
    def test_parent_death_releases_warm_residency(
        self, db_session, residency, tmp_path, _linux
    ):
        from app.runtime import inference_resources

        assets = local_embedding_assets(tmp_path / "orphan-assets")
        env = os.environ.copy()
        env.pop("COVERAGE_PROCESS_CONFIG", None)
        parent = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.inference_residency_parent",
                str(assets),
                str(residency),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=BACKEND_DIR,
            env=env,
        )
        child_identity = None
        held_input = None
        try:
            assert parent.stdout is not None
            assert _readable(parent.stdout, 10), "ONNX parent not ready"
            response = parent.stdout.readline()
            if not response:
                parent.wait(timeout=5)
                assert parent.stderr is not None
                pytest.fail(parent.stderr.read().decode())
            report = json.loads(response)
            assert report["vector"] == [1.0, 0.0, 0.0]
            child_identity = os.pidfd_open(report["worker_pid"])
            # Retain a pipe writer so EOF alone cannot prove parent-death containment.
            held_input = os.open(
                f"/proc/{parent.pid}/fd/{report['worker_stdin_fd']}", os.O_WRONLY
            )
            parent.kill()
            parent.wait(timeout=5)
            assert _readable(child_identity, 5), "warm child survived parent"
            context = InferenceContext.bounded(5)
            with inference_resources.reserve(checkpoint=context.remaining) as permit:
                assert permit.resources.slots == 1
                assert (
                    0 < permit.resources.bytes <= inference_resources.capacity().bytes
                )
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=5)
            if held_input is not None:
                os.close(held_input)
            if child_identity is not None:
                os.close(child_identity)
            for stream in (parent.stdin, parent.stdout, parent.stderr):
                if stream is not None:
                    stream.close()


@pytest.fixture
def retirement_probe():
    from tests.paths import BACKEND_DIR

    children = []

    def start(case, *arguments):
        env = os.environ.copy()
        env.pop("COVERAGE_PROCESS_CONFIG", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.inference_retirement_probe",
                case,
                *map(str, arguments),
            ],
            cwd=BACKEND_DIR,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        children.append(process)
        return process

    yield start
    for process in children:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


class TestResidencyRetirementRace:
    def test_new_worker_handles_its_first_frame_before_retirement(
        self, resident_provider, retirement_probe
    ):
        from app.modules.inference.worker_protocol import WorkerRequest

        provider = resident_provider
        process = retirement_probe("first-frame", provider.directory)
        assert process.stderr is not None and process.stdout is not None
        assert _readable(process.stderr, 5)
        assert process.stderr.readline() == b"ready\n"
        # A new worker must wait for its first frame despite already queued pressure.
        assert not _readable(process.stdout, 0.3)
        request = (
            WorkerRequest(
                config_hash=provider.space.config_hash,
                space_json=json.dumps(provider.space.__dict__),
                inputs=[{"modality": "text", "text": "red"}],
            )
            .model_dump_json()
            .encode()
        )
        response = provider._exchange(process, request, InferenceContext.bounded(5))
        assert json.loads(response)["vectors"] == [[1.0, 0.0, 0.0]]
        assert process.wait(timeout=5) == 73

    def test_accepts_complete_buffered_reply_after_idle_retirement(
        self, resident_provider, retirement_probe, monkeypatch
    ):
        provider = resident_provider
        children = []

        def spawn(**_kwargs):
            process = retirement_probe("reply")
            children.append(process)
            real_poll = process.poll
            polls = 0

            def after_reply():
                nonlocal polls
                polls += 1
                if polls == 2:
                    assert process.wait(timeout=5) == 73
                return real_poll()

            monkeypatch.setattr(process, "poll", after_reply)
            return process

        monkeypatch.setattr(provider, "_spawn", spawn)
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space
        ) == ((1.0, 0.0, 0.0),)
        assert len(children) == 1

    @pytest.mark.parametrize(
        "supplied_context", [False, True], ids=["default", "explicit"]
    )
    def test_retries_classified_retirement_once_with_same_context(
        self, resident_provider, retirement_probe, monkeypatch, supplied_context
    ):
        provider = resident_provider
        original_spawn = provider._spawn
        contexts = []
        context = InferenceContext.bounded(5) if supplied_context else None

        def spawn(*, context=None, deadline=None):
            contexts.append(context)
            if len(contexts) == 1:
                process = retirement_probe("retire")
                assert process.wait(timeout=5) == 73
                return process
            assert len(contexts) == 2, "retirement retried more than once"
            return original_spawn(context=context, deadline=deadline)

        monkeypatch.setattr(provider, "_spawn", spawn)
        assert provider.embed(
            (EmbeddingInput("text", text="red"),), provider.space, context=context
        ) == ((1.0, 0.0, 0.0),)
        assert len(contexts) == 2
        assert contexts[0] is contexts[1]
        assert contexts[0] is not None
        if context is not None:
            assert contexts[0].deadline == context.deadline

    def test_repeated_retirement_yields_busy_without_a_third_spawn(
        self, resident_provider, retirement_probe, monkeypatch
    ):
        from printstash_core.inference import EmbeddingError

        children = []

        def spawn(**_kwargs):
            assert len(children) < 2, "unbounded retirement replay"
            process = retirement_probe("retire")
            children.append(process)
            assert process.wait(timeout=5) == 73
            return process

        monkeypatch.setattr(resident_provider, "_spawn", spawn)
        with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
            resident_provider.embed(
                (EmbeddingInput("text", text="red"),),
                resident_provider.space,
                context=InferenceContext.bounded(5),
            )
        assert len(children) == 2

    @pytest.mark.parametrize(
        "case,code",
        [
            ("partial", "embedding_inference_failed"),
            ("malformed", "embedding_output_invalid"),
            ("oom", "embedding_worker_oom"),
            ("failed", "embedding_inference_failed"),
        ],
    )
    def test_never_replays_unaccepted_or_invalid_native_outcomes(
        self, resident_provider, retirement_probe, monkeypatch, case, code
    ):
        from printstash_core.inference import EmbeddingError

        children = []

        def spawn(**_kwargs):
            assert not children, "native outcome was replayed"
            process = retirement_probe(case)
            children.append(process)
            return process

        monkeypatch.setattr(resident_provider, "_spawn", spawn)
        with pytest.raises(EmbeddingError, match=code):
            resident_provider.embed(
                (EmbeddingInput("text", text="red"),),
                resident_provider.space,
                context=InferenceContext.bounded(5),
            )
        assert len(children) == 1

    def test_retirement_retry_preserves_the_absolute_exchange_deadline(
        self, resident_provider, retirement_probe, monkeypatch
    ):
        from types import SimpleNamespace

        from printstash_core.inference import EmbeddingError

        from app.modules.inference import local

        clock = {"now": 0.0}
        monkeypatch.setattr(
            local, "time", SimpleNamespace(monotonic=lambda: clock["now"])
        )
        original_exchange = resident_provider._exchange
        children = []

        def exchange(process, payload, context, **kwargs):
            try:
                return original_exchange(process, payload, context, **kwargs)
            finally:
                clock["now"] = 100.0  # Exceeds the original <=90s exchange allowance.

        def spawn(**_kwargs):
            assert not children, "new worker spawned after original exchange deadline"
            process = retirement_probe("retire")
            children.append(process)
            assert process.wait(timeout=5) == 73
            return process

        monkeypatch.setattr(resident_provider, "_spawn", spawn)
        monkeypatch.setattr(resident_provider, "_exchange", exchange)
        with pytest.raises(EmbeddingError, match="embedding_timeout"):
            resident_provider.embed(
                (EmbeddingInput("text", text="red"),),
                resident_provider.space,
                context=InferenceContext.bounded(5),
            )
        assert len(children) == 1


class TestResidencyPoolCoordination:
    @pytest.mark.parametrize("stop", ["deadline", "cancel"], ids=["deadline", "cancel"])
    def test_active_cleanup_precedes_another_model_residency_deadline(
        self, residency, tmp_path, stop
    ):
        from printstash_core.inference import EmbeddingError

        from app.modules.inference.worker_pool import WorkerPool
        from app.modules.media.worker_bootstrap import command
        from app.runtime import inference_resources
        from app.runtime.native_admission import LocalResourcePool

        workers = WorkerPool(capacity=2)
        active = threading.Event()
        queued = threading.Event()
        cancel_first = threading.Event()
        cancel_second = threading.Event()
        children = []
        first_context = InferenceContext.bounded(1.5, cancelled=cancel_first.is_set)
        second_context = InferenceContext.bounded(5, cancelled=cancel_second.is_set)

        def start(context, *, report_queue=False):
            def checkpoint():
                context.remaining()
                if report_queue and LocalResourcePool(residency).has_waiters(
                    checkpoint=context.remaining
                ):
                    queued.set()

            with inference_resources.reserve(checkpoint=checkpoint) as permit:
                launch = inference_resources.launch_resources(permit)
                env = os.environ.copy()
                env.pop("COVERAGE_PROCESS_CONFIG", None)
                env.pop("PRINTSTASH_PREPARATION_PERMIT_FD", None)
                env.pop("PRINTSTASH_PREPARATION_PERMIT_PATH", None)
                env.update(launch.environment)
                process = subprocess.Popen(
                    command(
                        "tests.fakes.inference_pool_stall", [], permit.resources.bytes
                    ),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=BACKEND_DIR,
                    env=env,
                    pass_fds=launch.descriptors,
                    start_new_session=True,
                )
                children.append(process)
                return process

        def first():
            with workers.acquire(
                ("first",),
                tmp_path / "first",
                lambda: start(first_context),
                first_context,
            ):
                active.set()
                assert queued.wait(3), "second model never queued for residency"
                if stop == "cancel":
                    cancel_first.set()
                while True:
                    first_context.remaining()
                    cancel_first.wait(0.01)

        def second():
            with workers.acquire(
                ("second",),
                tmp_path / "second",
                lambda: start(second_context, report_queue=True),
                second_context,
            ) as process:
                return process.pid

        with ThreadPoolExecutor(2) as executor:
            first_result = executor.submit(first)
            second_result = None
            try:
                assert active.wait(2)
                second_result = executor.submit(second)
                assert queued.wait(2)
                stopped_at = time.monotonic()
                reason = (
                    "inference_timeout" if stop == "deadline" else "inference_cancelled"
                )
                with pytest.raises(EmbeddingError, match=reason):
                    first_result.result(timeout=2)
                assert time.monotonic() - stopped_at < 2
                assert children[0].poll() is not None
                assert second_result.result(timeout=2) != children[0].pid
                assert not cancel_second.is_set()
            finally:
                cancel_first.set()
                cancel_second.set()
                for future in (first_result, second_result):
                    if future is not None:
                        try:
                            future.result(timeout=6)
                        except Exception:
                            pass
                workers.close()
                for process in children:
                    assert process.poll() is not None

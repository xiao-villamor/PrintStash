"""Private benchmark cleanup retires intent before waiting for physical owners."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Event

import httpx
import pytest

from app.db.models import JobKind, JobState
from app.modules.media import mesh_isolation
from app.modules.storage.storage_backend import generations
from app.modules.work.contracts import Step
from app.modules.work.runner import ExecutionContext, StepOutcome, _run_step
from app.runtime import maintenance
from scripts.benchmark_cleanup import cleanup_private_jobs


@pytest.fixture
def private_client(client, auth_headers):
    client.headers.update(auth_headers)
    try:
        yield client
    finally:
        maintenance.end_restore_maintenance()


class TestCleanupPrivateJobs:
    def test_gates_an_idle_vault(self, private_client):
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is True
        assert result.active_mutations == 0
        assert result.active_readers is False
        assert result.cancelled == ()
        assert result.errors == ()
        assert result.elapsed_ms >= 0
        assert maintenance.begin_mutating_operation() is False
        maintenance.end_restore_maintenance()
        assert maintenance.begin_mutating_operation() is True
        maintenance.end_mutating_operation()

    def test_withdraws_queued_private_work(
        self, private_client, make_job, make_file, make_model, make_user, db_session
    ):
        user = make_user()
        user_job = make_job(owner=user)
        file = make_file(make_model())
        system = make_job(kind=JobKind.DERIVATIVES_MESH, subject=f"file/{file.id}")
        terminal = make_job(state=JobState.COMPLETED)
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is True
        assert {(entry.job_id, entry.kind) for entry in result.cancelled} == {
            (user_job.id, user_job.kind),
            (system.id, system.kind),
        }
        for job in (user_job, system):
            db_session.refresh(job)
            assert job.state is JobState.CANCELLED
        db_session.refresh(terminal)
        assert terminal.state is JobState.COMPLETED
        assert result.errors == ()

    def test_reaps_a_cancelled_native_child(
        self, threaded_hub_db, private_client, make_job, monkeypatch
    ):
        job = make_job(state=JobState.RUNNING, attempts=1)
        spawned = Event()
        processes = []
        real_popen = subprocess.Popen

        def observed_spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            spawned.set()
            return process

        monkeypatch.setattr(mesh_isolation.subprocess, "Popen", observed_spawn)

        def native_step(_context):
            with generations.use(generations.pin()):
                mesh_isolation.supervise_result(
                    [sys.executable, "-c", "import signal; signal.pause()"],
                    memory_budget=512 * 1024**2,
                    timeout_seconds=60,
                )

        context = ExecutionContext(
            job_id=job.id,
            definition=job.kind,
            subject_key=job.subject_key,
            priority=job.priority,
            execution_id=f"native/{job.id}",
            attempt=job.attempts,
            execution_epoch=job.execution_epoch,
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                copy_context().run,
                _run_step,
                Step("private.native", native_step),
                context,
                mutating=True,
            )
            try:
                assert spawned.wait(5), "native child was never spawned"
                result = cleanup_private_jobs(private_client, deadline_seconds=5)
                assert result.quiescent is True, result
                assert future.result(timeout=5) == StepOutcome.CANCELLED.value
                assert processes[0].poll() is not None
                assert result.active_mutations == 0
                assert result.active_readers is False
                assert result.cancelled[0].job_id == job.id
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

    def test_accepts_a_terminal_cancellation_race(
        self, private_client, make_job, monkeypatch, db_session
    ):
        job = make_job()
        real_post = private_client.post

        def completed_before_cancel(url, **kwargs):
            first = real_post(url, **kwargs)
            assert first.status_code == 200
            return real_post(url, **kwargs)

        monkeypatch.setattr(private_client, "post", completed_before_cancel)
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is True
        assert result.cancelled == ()
        assert result.errors == ()
        db_session.refresh(job)
        assert job.state is JobState.CANCELLED

    def test_cancels_work_admitted_before_the_final_gate(
        self, private_client, make_job, monkeypatch, db_session
    ):
        real_get = private_client.get
        appeared = []

        def concurrent_admission(url, **kwargs):
            if maintenance.restore_in_progress() and not appeared:
                appeared.append(make_job())
            return real_get(url, **kwargs)

        monkeypatch.setattr(private_client, "get", concurrent_admission)
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is True
        assert [entry.job_id for entry in result.cancelled] == [appeared[0].id]
        db_session.refresh(appeared[0])
        assert appeared[0].state is JobState.CANCELLED
        assert maintenance.begin_mutating_operation() is False

    def test_retains_mutation_timeout_evidence(self, private_client):
        assert maintenance.begin_mutating_operation()
        try:
            result = cleanup_private_jobs(private_client, deadline_seconds=0)
            assert result.quiescent is False
            assert result.active_mutations == 1
            assert result.errors
            assert maintenance.begin_mutating_operation() is False
        finally:
            maintenance.end_mutating_operation()

    def test_retains_reader_timeout_evidence(self, private_client):
        lease = generations.pin()
        try:
            result = cleanup_private_jobs(private_client, deadline_seconds=0)
            assert result.quiescent is False
            assert result.active_mutations == 0
            assert result.active_readers is True
            assert result.errors
            assert maintenance.begin_mutating_operation() is False
        finally:
            lease.close()

    @pytest.mark.parametrize(
        ("status", "payload"),
        [
            pytest.param(503, {"detail": "unavailable"}, id="unavailable"),
            pytest.param(200, {"not": "jobs"}, id="malformed-list"),
        ],
    )
    def test_retains_listing_failure_evidence(
        self, private_client, monkeypatch, status, payload
    ):
        monkeypatch.setattr(
            private_client,
            "get",
            lambda *_args, **_kwargs: httpx.Response(status, json=payload),
        )
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is False
        assert result.errors
        assert maintenance.begin_mutating_operation() is False

    def test_retains_transport_failure_evidence(self, private_client, monkeypatch):
        def disconnected(*_args, **_kwargs):
            raise httpx.TransportError("private transport stopped")

        monkeypatch.setattr(private_client, "get", disconnected)
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is False
        assert "private transport stopped" in result.errors[0]
        assert maintenance.begin_mutating_operation() is False

    def test_retains_cancel_failure_evidence(
        self, private_client, make_job, monkeypatch, db_session
    ):
        job = make_job()
        monkeypatch.setattr(
            private_client,
            "post",
            lambda *_args, **_kwargs: httpx.Response(503, json={"detail": "offline"}),
        )
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is False
        assert result.cancelled == ()
        assert result.errors
        db_session.refresh(job)
        assert job.state is JobState.QUEUED
        assert maintenance.begin_mutating_operation() is False

    def test_retries_an_active_cancellation_conflict(
        self, private_client, make_job, monkeypatch
    ):
        job = make_job()
        real_post = private_client.post
        first = True

        def conflicted_once(url, **kwargs):
            nonlocal first
            if first:
                first = False
                return httpx.Response(409, json={"detail": "job_not_active"})
            return real_post(url, **kwargs)

        monkeypatch.setattr(private_client, "post", conflicted_once)
        result = cleanup_private_jobs(private_client)
        assert result.quiescent is True
        assert [entry.job_id for entry in result.cancelled] == [job.id]
        assert result.errors == ()

    @pytest.mark.parametrize(
        "deadline",
        [
            pytest.param(-1, id="negative"),
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("inf"), id="infinite"),
        ],
    )
    def test_rejects_invalid_cleanup_deadlines(self, private_client, deadline):
        with pytest.raises(ValueError, match="finite.*nonnegative"):
            cleanup_private_jobs(private_client, deadline_seconds=deadline)
        assert maintenance.restore_in_progress() is False

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import BackgroundJob
from app.db.session import get_session_factory
from app.services import jobs as jobs_module
from app.services.jobs import JobRegistry, reconcile_interrupted_jobs


def test_progress_hints_round_trip() -> None:
    registry = JobRegistry()
    job_id = registry.create()

    registry.update(job_id, state="running", total_steps=5)
    registry.update(job_id, step=2, total_steps=5, label="loading_mesh", progress=20.0)

    job = registry.get(job_id)
    assert job is not None
    assert job.state == "running"
    assert job.step == 2
    assert job.total_steps == 5
    assert job.label == "loading_mesh"
    assert job.progress == 20.0
    assert job.started_at is not None


def test_progress_clamped_and_completed_forces_100() -> None:
    registry = JobRegistry()
    job_id = registry.create()

    registry.update(job_id, progress=250.0)
    assert registry.get(job_id).progress == 100.0

    registry.update(job_id, progress=-3.0)
    assert registry.get(job_id).progress == 0.0

    registry.update(job_id, state="completed")
    job = registry.get(job_id)
    assert job.progress == 100.0
    assert job.finished_at is not None


def test_result_payload_stored() -> None:
    registry = JobRegistry()
    job_id = registry.create()
    registry.update(job_id, state="completed", result={"rebuilt": [1, 2]})
    assert registry.get(job_id).result == {"rebuilt": [1, 2]}


def test_finished_jobs_pruned_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic_now = 0.0
    monkeypatch.setattr(jobs_module, "monotonic", lambda: monotonic_now)
    registry = JobRegistry()
    old_id = registry.create()
    registry.update(old_id, state="completed")
    # Age the finished job past the TTL, then trigger pruning via create().
    expired_at = utcnow() - timedelta(hours=2)
    registry.get(old_id).finished_at = expired_at
    with get_session_factory().scoped_session() as session:
        row = session.get(BackgroundJob, old_id)
        assert row is not None
        row.finished_at = expired_at
        session.add(row)
        session.commit()

    monotonic_now = 61.0
    fresh_id = registry.create()

    assert registry.get(old_id) is None
    assert registry.get(fresh_id) is not None


def test_running_jobs_never_pruned() -> None:
    registry = JobRegistry()
    running_id = registry.create()
    registry.update(running_id, state="running")
    registry.get(running_id).started_at = utcnow() - timedelta(hours=5)

    registry.create()

    assert registry.get(running_id) is not None


def test_job_status_survives_registry_recreation() -> None:
    first = JobRegistry()
    job_id = first.create(owner_user_id=7)
    first.update(job_id, state="running", label="persisted", progress=25)

    restored = JobRegistry().get(job_id)

    assert restored is not None
    assert restored.owner_user_id == 7
    assert restored.state == "running"
    assert restored.label == "persisted"
    assert restored.progress == 25


def test_restart_marks_interrupted_non_replayable_job_retryable() -> None:
    registry = JobRegistry()
    job_id = registry.create(owner_user_id=7)
    registry.update(job_id, state="running", label="upload")

    assert reconcile_interrupted_jobs() == 1
    restored = JobRegistry().get(job_id)

    assert restored is not None
    assert restored.state == "failed"
    assert restored.error == "interrupted_by_restart"
    assert restored.retryable is True

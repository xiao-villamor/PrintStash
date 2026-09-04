"""The job record a user watches, and what pruning it must not take with it.

A background job's row is the only window into work that outlives the request
that started it, so its progress has to behave like progress: monotonic below a
terminal state, and forced to 100% once complete. A bar that goes backwards, or
one that sits at 97% on a finished job, reads as a stuck import.

Pruning is where the real risk is. Finished jobs are deleted after a TTL, and the
rows here exist because two other things point at them:

* A **completed Inbox item** references its job. Pruning must not leave that
  reference dangling, or the import history stops loading.
* A **job-owned staging lease** is what proves PrintStash owns the staged bytes a
  retry would use. Pruning the job while keeping the lease is the safe direction;
  dropping the lease means the retry has no provable claim on its own files and
  is refused.

So the pruning tests assert on what *survives*, not just on what is removed —
which is the only way to catch a cascade that takes a neighbour with it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    InboxItem,
    InboxItemState,
    StagingLease,
    User,
)
from app.db.session import get_session_factory
from app.services import jobs as jobs_module
from app.services.jobs import JobRegistry, reconcile_interrupted_jobs
from tests.factories import build_user


@pytest.fixture
def owner(db_session: Session) -> User:
    """The user these background jobs belong to.

    `background_jobs.owner_user_id` is a foreign key, so a job owned by an id that
    does not exist is refused here as it is in production.
    """
    return build_user(db_session, "job-owner")


class TestJobRegistry:
    """Recording a background job's progress and outcome so a page can follow it."""

    def test_progress_hints_round_trip(self) -> None:
        registry = JobRegistry()
        job_id = registry.create()

        registry.update(job_id, state="running", total_steps=5)
        registry.update(
            job_id, step=2, total_steps=5, label="loading_mesh", progress=20.0
        )

        job = registry.get(job_id)
        assert job is not None
        assert job.state == "running"
        assert job.step == 2
        assert job.total_steps == 5
        assert job.label == "loading_mesh"
        assert job.progress == 20.0
        assert job.started_at is not None

    def test_progress_over_range_is_clamped_below_completion(self) -> None:
        # 99.0 rather than 100.0: only reaching a terminal state may report 100, so a
        # provider sending 250 must not make the UI say the print finished.
        registry = JobRegistry()
        job_id = registry.create()

        registry.update(job_id, progress=250.0)

        assert registry.get(job_id).progress == 99.0

    def test_progress_never_moves_backwards(self) -> None:
        # Splitting the old combined test exposed that this is monotonicity, not a
        # lower clamp: a fresh job handed -3.0 reports 0.0, and the 99.0 below is the
        # earlier value being *kept* rather than a floor being applied.
        registry = JobRegistry()
        job_id = registry.create()
        registry.update(job_id, progress=250.0)

        registry.update(job_id, progress=-3.0)

        assert registry.get(job_id).progress == 99.0

    def test_completing_a_job_forces_progress_to_one_hundred(self) -> None:
        registry = JobRegistry()
        job_id = registry.create()
        registry.update(job_id, progress=250.0)

        registry.update(job_id, state="completed")

        job = registry.get(job_id)
        assert job.progress == 100.0
        assert job.finished_at is not None

    def test_result_payload_stored(self) -> None:
        registry = JobRegistry()
        job_id = registry.create()
        registry.update(job_id, state="completed", result={"rebuilt": [1, 2]})
        assert registry.get(job_id).result == {"rebuilt": [1, 2]}

    def test_terminal_state_cannot_regress_or_be_overwritten(self) -> None:
        registry = JobRegistry()
        job_id = registry.create()
        registry.update(job_id, state="running", progress=20)
        registry.finish(job_id, state="completed", result={"winner": "first"})
        registry.update(job_id, state="running", progress=30)
        registry.finish(job_id, state="failed", result={"winner": "late"})

        restored = JobRegistry().get(job_id)
        assert restored is not None
        assert restored.state == "completed"
        assert restored.result == {"winner": "first"}
        assert restored.progress == 100

    def test_job_status_survives_registry_recreation(self, owner: User) -> None:
        first = JobRegistry()
        job_id = first.create(owner_user_id=owner.id)
        first.update(job_id, state="running", label="persisted", progress=25)

        restored = JobRegistry().get(job_id)

        assert restored is not None
        assert restored.owner_user_id == owner.id
        assert restored.state == "running"
        assert restored.label == "persisted"
        assert restored.progress == 25

    def test_running_jobs_never_pruned(self) -> None:
        registry = JobRegistry()
        running_id = registry.create()
        registry.update(running_id, state="running")
        registry.get(running_id).started_at = utcnow() - timedelta(hours=5)

        registry.create()

        assert registry.get(running_id) is not None

    def test_finished_jobs_pruned_after_ttl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_finished_job_pruning_keeps_completed_inbox_reference_valid(
        self, owner: User, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A terminal capture may retain its job link after staging cleanup."""
        monotonic_now = 0.0
        monkeypatch.setattr(jobs_module, "monotonic", lambda: monotonic_now)
        owner = build_user(db_session, "prune-inbox-owner")
        db_session.refresh(owner)

        registry = JobRegistry()
        old_id = registry.create(owner_user_id=owner.id)
        registry.update(old_id, state="completed")
        expired_at = utcnow() - timedelta(hours=2)
        with get_session_factory().scoped_session() as session:
            job = session.get(BackgroundJob, old_id)
            assert job is not None
            job.finished_at = expired_at
            session.add(
                InboxItem(
                    owner_user_id=owner.id,
                    state=InboxItemState.COMPLETED,
                    background_job_id=old_id,
                )
            )
            session.add(job)
            session.commit()

        monotonic_now = 61.0
        fresh_id = registry.create(owner_user_id=owner.id)

        assert registry.get(fresh_id) is not None

    def test_finished_job_pruning_keeps_job_owned_staging_lease_for_retry(
        self, owner: User, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cleanup-pending imports retain terminal jobs while staging is owned."""
        monotonic_now = 0.0
        monkeypatch.setattr(jobs_module, "monotonic", lambda: monotonic_now)
        owner = build_user(db_session, "prune-lease-owner")
        db_session.refresh(owner)

        registry = JobRegistry()
        old_id = registry.create(owner_user_id=owner.id)
        registry.update(old_id, state="completed")
        expired_at = utcnow() - timedelta(hours=2)
        with get_session_factory().scoped_session() as session:
            job = session.get(BackgroundJob, old_id)
            assert job is not None
            job.finished_at = expired_at
            inbox_item = InboxItem(
                owner_user_id=owner.id,
                state=InboxItemState.COMPLETED,
                background_job_id=old_id,
                retryable=True,
                error_code="capture_upload_cleanup_pending",
            )
            session.add(inbox_item)
            session.add(
                StagingLease(
                    id="cleanup-pending-lease",
                    path="staging/cleanup-pending.stl",
                    owner_user_id=owner.id,
                    background_job_id=old_id,
                    size_bytes=4,
                    sha256="f" * 64,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
            session.add(job)
            session.commit()

        monotonic_now = 61.0
        fresh_id = registry.create(owner_user_id=owner.id)

        assert registry.get(fresh_id) is not None
        with get_session_factory().scoped_session() as session:
            assert session.get(BackgroundJob, old_id) is not None
            assert (
                session.exec(
                    select(StagingLease).where(StagingLease.background_job_id == old_id)
                )
                .one()
                .id
                == "cleanup-pending-lease"
            )


class TestReconcileInterruptedJobs:
    """What a restart does to jobs that were still running when the process died."""

    def test_restart_marks_interrupted_non_replayable_job_retryable(
        self, owner: User
    ) -> None:
        registry = JobRegistry()
        job_id = registry.create(owner_user_id=owner.id)
        registry.update(job_id, state="running", label="upload")

        assert reconcile_interrupted_jobs() == 1
        restored = JobRegistry().get(job_id)

        assert restored is not None
        assert restored.state == "failed"
        assert restored.error == "interrupted_by_restart"
        assert restored.retryable is True

    def test_restart_fails_pending_job_even_when_replay_safe(self, owner: User) -> None:
        registry = JobRegistry()
        job_id = registry.create(owner_user_id=owner.id)
        with get_session_factory().scoped_session() as session:
            row = session.get(BackgroundJob, job_id)
            assert row is not None
            row.replay_safe = True
            session.add(row)
            session.commit()

        assert reconcile_interrupted_jobs() == 1
        restored = JobRegistry().get(job_id)
        assert restored is not None
        assert restored.state == "failed"
        assert restored.retryable is True

"""Submitting a Job's next attempt, and nudging the reconciler.

``submit`` hands the next attempt to the engine and records it only once the
engine accepted it, so a crash in between re-derives the same execution id.
``nudge`` is how hot paths ask for work: it stamps the dirty mark and enqueues
a reconcile pass unless one of at least its priority is already waiting. It
never raises into the caller, because the tick recovers a lost nudge.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import JobKind, JobState, ReconcileCursor, WorkPriority
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import (
    Deduplicated,
    Partitioned,
    PassSubmission,
    SubmitOutcome,
)
from app.modules.work.submission import (
    execution_id,
    forget_queued_passes,
    nudge,
    nudge_after_commit,
    nudge_all,
    submit,
)


def _passes(engine, source: JobKind) -> list:
    return [
        execution
        for execution in engine.executions.values()
        if isinstance(execution.submission, PassSubmission)
        and execution.submission.source == source
    ]


def _cursor(session: Session, source: JobKind) -> ReconcileCursor:
    session.expire_all()
    cursor = session.get(ReconcileCursor, source)
    assert cursor is not None
    return cursor


class TestSubmit:
    @pytest.mark.parametrize(
        "outcome,fields", [("completed", {}), ("failed", {"error": "expected"})]
    )
    def test_terminal_execution_before_submit_returns_keeps_its_engine_result(
        self, work_engine, make_job, db_session, monkeypatch, outcome, fields
    ):
        from app.db.models import LaneName
        from app.modules.work.catalog import default_lanes
        from app.modules.work.contracts import (
            EngineStatus,
            JobDefinition,
            JobOutcome,
            JobSubmission,
            Step,
        )
        from app.runtime.engine.inline import InlineJobEngine

        def finish(ctx):
            ctx.finish(JobOutcome(outcome), **fields)

        catalog = WorkCatalog(
            [
                JobDefinition(
                    name=JobKind.SOURCES_SCAN,
                    lane=LaneName.MAINTENANCE,
                    steps=(Step("finish-before-ack", finish),),
                    label="Probe",
                )
            ],
            lanes=default_lanes(),
        )
        engine = InlineJobEngine(catalog)
        engine.launch(listen_lanes=None)
        catalog_module.bind(engine, catalog)
        job = make_job(kind=JobKind.SOURCES_SCAN)
        accepted = engine.submit
        cancellations = []
        cancel = engine.cancel

        def finish_before_ack(submission):
            result = accepted(submission)
            if isinstance(submission, JobSubmission):
                engine.drain()
            return result

        def record_cancel(execution):
            cancellations.append(execution)
            cancel(execution)

        monkeypatch.setattr(engine, "submit", finish_before_ack)
        monkeypatch.setattr(engine, "cancel", record_cancel)
        assert submit(job.id) is SubmitOutcome.ACCEPTED

        db_session.refresh(job)
        assert job.state.value == outcome
        assert job.attempts == 1
        assert job.submitted_epoch == job.execution_epoch
        execution = execution_id(job.id, 1, job.execution_epoch)
        assert engine.evidence([execution])[execution].status is EngineStatus.SUCCEEDED
        assert cancellations == []

    def test_an_accepted_submission_counts_as_the_next_attempt(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=JobKind.SOURCES_SCAN)

        outcome = submit(job.id)

        assert outcome is SubmitOutcome.ACCEPTED
        db_session.refresh(job)
        assert job.attempts == 1
        execution = work_engine.executions[execution_id(job.id, 1, job.execution_epoch)]
        assert execution.submission.routing == Deduplicated(
            f"sources.scan|{job.subject_key}"
        )

    def test_an_interrupted_job_is_queued_again_on_resubmission(
        self, work_engine, make_job, db_session: Session
    ) -> None:
        job = make_job(
            kind=JobKind.SOURCES_SCAN, state=JobState.INTERRUPTED, attempts=1
        )

        submit(job.id)

        db_session.refresh(job)
        assert (job.state, job.attempts) == (JobState.QUEUED, 2)

    @pytest.mark.parametrize(
        "state", [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED]
    )
    def test_a_settled_job_has_nothing_to_submit(
        self, work_engine, make_job, state: JobState
    ) -> None:
        job = make_job(kind=JobKind.SOURCES_SCAN, state=state)

        assert submit(job.id) is None
        assert work_engine.executions == {}

    def test_a_missing_job_has_nothing_to_submit(self, work_engine) -> None:
        assert submit("no-such-job") is None

    def test_a_deduplicated_attempt_is_not_recorded(
        self, work_engine, make_job, db_session: Session, monkeypatch
    ) -> None:
        job = make_job(kind=JobKind.SOURCES_SCAN)
        monkeypatch.setattr(
            work_engine, "submit", lambda _submission: SubmitOutcome.DEDUPLICATED
        )

        assert submit(job.id) is SubmitOutcome.DEDUPLICATED
        db_session.refresh(job)
        assert job.attempts == 0

    def test_a_partitioned_lane_is_keyed_by_partition_not_dedupe(
        self, work_engine, make_job
    ) -> None:
        # Engines cannot deduplicate a partitioned queue; the active-subject
        # claim on the Job row is what keeps one delivery single-flight.
        job = make_job(
            kind=JobKind.NOTIFICATIONS_DELIVER, subject="channel/7/delivery/9"
        )

        submit(job.id)

        submission = work_engine.executions[
            execution_id(job.id, 1, job.execution_epoch)
        ].submission
        assert submission.routing == Partitioned("7")

    def test_retokened_backfill_cannot_submit_a_reserved_epoch(
        self, work_engine, make_job, make_user, make_model, make_file, db_session
    ):
        from app.db.models import Job
        from app.modules.derivatives.source import subject_key
        from app.modules.work import service

        actor = make_user()
        first_file = make_file(make_model(), filename="first.stl")
        reserved_epoch = "first-reserved-backfill"
        first = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(first_file.id),
            owner=actor,
            priority=WorkPriority.BACKFILL,
            execution_epoch=reserved_epoch,
            backfill_admission_epoch=reserved_epoch,
        )
        first_id = first.id
        service.cancel(first_id, actor=actor)
        service.retry(first_id, actor=actor)
        db_session.expire_all()
        retokened = db_session.get(Job, first_id)
        assert retokened is not None
        assert retokened.execution_epoch != reserved_epoch
        second_file = make_file(make_model(), filename="second.stl")
        other_epoch = "second-reserved-backfill"
        second = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=subject_key(second_file.id),
            owner=actor,
            priority=WorkPriority.BACKFILL,
            execution_epoch=other_epoch,
            backfill_admission_epoch=other_epoch,
        )
        second_id = second.id
        assert (
            submit(second_id, reserved_backfill_epoch=other_epoch)
            is SubmitOutcome.ACCEPTED
        )

        outcome = submit(first_id, reserved_backfill_epoch=reserved_epoch)

        assert outcome is None
        db_session.expire_all()
        assert db_session.get(Job, first_id).attempts == 0
        assert db_session.get(Job, second_id).attempts == 1
        assert execution_id(second_id, 1, other_epoch) in work_engine.executions
        assert (
            execution_id(first_id, 1, retokened.execution_epoch)
            not in work_engine.executions
        )

    def test_unreserved_current_backfill_epoch_cannot_submit(
        self, work_engine, make_job, db_session
    ):
        job = make_job(kind=JobKind.SOURCES_SCAN, priority=WorkPriority.BACKFILL)
        job_id, current_epoch = job.id, job.execution_epoch

        outcome = submit(job_id, reserved_backfill_epoch=current_epoch)

        assert outcome is None
        db_session.refresh(job)
        assert job.execution_epoch == current_epoch
        assert job.backfill_admission_epoch is None
        assert job.attempts == 0
        assert job.submitted_epoch is None
        assert work_engine.executions == {}


class TestNudge:
    def test_a_nudge_queues_a_pass_of_a_dirty_source(
        self, work_engine, db_session: Session
    ) -> None:
        nudge(JobKind.SOURCES_SCAN)

        cursor = _cursor(db_session, JobKind.SOURCES_SCAN)
        assert cursor.nudged_at is not None
        assert cursor.pass_queued_at is not None
        (queued,) = _passes(work_engine, JobKind.SOURCES_SCAN)
        assert queued.submission.priority is WorkPriority.INTERACTIVE

    def test_a_second_nudge_rides_on_the_queued_pass(self, work_engine) -> None:
        nudge(JobKind.SOURCES_SCAN)
        nudge(JobKind.SOURCES_SCAN)

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_a_backfill_nudge_rides_on_any_queued_pass(self, work_engine) -> None:
        nudge(JobKind.SOURCES_SCAN)
        nudge(JobKind.SOURCES_SCAN, priority=WorkPriority.BACKFILL)

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_an_interactive_nudge_does_not_wait_behind_a_backfill_pass(
        self, work_engine
    ) -> None:
        # Regression: an upload just after startup waited behind the startup
        # sweep's backfill pass for the same source.
        nudge(JobKind.SOURCES_SCAN, priority=WorkPriority.BACKFILL)

        nudge(JobKind.SOURCES_SCAN)

        priorities = sorted(
            execution.submission.priority.value
            for execution in _passes(work_engine, JobKind.SOURCES_SCAN)
        )
        assert priorities == ["backfill", "interactive"]

    def test_a_pass_queued_longer_than_the_grace_is_presumed_lost(
        self, work_engine, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source=JobKind.SOURCES_SCAN,
                pass_queued_at=utcnow()
                - timedelta(seconds=settings.jobs_submit_grace_seconds + 1),
                pass_priority=WorkPriority.INTERACTIVE,
            )
        )
        db_session.commit()

        nudge(JobKind.SOURCES_SCAN)

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_a_delayed_nudge_leaves_the_dirty_mark_alone(
        self, work_engine, db_session: Session
    ) -> None:
        nudge(JobKind.SOURCES_SCAN, delay=45)

        # The work it waits for is not due yet: nothing is marked dirty.
        assert db_session.get(ReconcileCursor, JobKind.SOURCES_SCAN) is None
        (delayed,) = _passes(work_engine, JobKind.SOURCES_SCAN)
        assert delayed.submission.delay_seconds == 45

    def test_does_nothing_without_a_bound_engine(
        self, work_engine, db_session: Session
    ) -> None:
        catalog_module.bind(None, None)

        nudge(JobKind.SOURCES_SCAN)

        assert db_session.exec(select(ReconcileCursor)).all() == []

    def test_an_unknown_source_never_raises_into_the_caller(
        self, work_engine, db_session: Session
    ) -> None:
        catalog_module.bind(work_engine, WorkCatalog())

        nudge(JobKind.SOURCES_SCAN)

        assert db_session.exec(select(ReconcileCursor)).all() == []

    def test_an_engine_that_refuses_never_raises_into_the_caller(
        self, work_engine, monkeypatch
    ) -> None:
        def refuse(_submission):
            raise RuntimeError("engine down")

        monkeypatch.setattr(work_engine, "submit", refuse)

        nudge(JobKind.SOURCES_SCAN)


class TestNudgeAfterCommit:
    def test_nudges_once_the_transaction_commits(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, JobKind.SOURCES_SCAN)
        assert _passes(work_engine, JobKind.SOURCES_SCAN) == []

        db_session.commit()

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_a_rolled_back_transaction_nudges_nothing(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, JobKind.SOURCES_SCAN)

        db_session.rollback()

        assert _passes(work_engine, JobKind.SOURCES_SCAN) == []

    def test_many_records_in_one_transaction_nudge_once(
        self, work_engine, db_session: Session
    ) -> None:
        # A bulk edit records hundreds of changes; its nudge is still one.
        for _ in range(3):
            nudge_after_commit(db_session, JobKind.SOURCES_SCAN)

        db_session.commit()

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_a_released_savepoint_waits_for_the_real_commit(
        self, work_engine, db_session: Session
    ) -> None:
        # SQLAlchemy reports a savepoint's release as a commit. Nudging there
        # writes the cursor on a second connection while this transaction
        # still holds SQLite's write lock: the nudge waits on its own caller.
        nudge_after_commit(db_session, JobKind.SOURCES_SCAN)
        with db_session.begin_nested():
            pass
        assert _passes(work_engine, JobKind.SOURCES_SCAN) == []

        db_session.commit()

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_a_rolled_back_savepoint_keeps_the_outer_nudge(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, JobKind.SOURCES_SCAN)
        nested = db_session.begin_nested()
        nested.rollback()

        db_session.commit()

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 1

    def test_the_next_transaction_nudges_again(
        self, work_engine, db_session: Session
    ) -> None:
        nudge_after_commit(db_session, JobKind.SOURCES_SCAN)
        db_session.commit()
        work_engine.drain()

        nudge_after_commit(db_session, JobKind.SOURCES_SCAN)
        db_session.commit()

        assert len(_passes(work_engine, JobKind.SOURCES_SCAN)) == 2


class TestNudgeAll:
    def test_nudges_every_definition_as_backfill(self, work_engine) -> None:
        nudge_all()

        submitted = {
            execution.submission.source: execution.submission.priority
            for execution in work_engine.executions.values()
        }
        assert set(submitted) == set(work_engine.catalog.definitions)
        assert set(submitted.values()) == {WorkPriority.BACKFILL}


class TestForgetQueuedPasses:
    def test_clears_only_the_marks_that_are_set(self, db_session: Session) -> None:
        db_session.add(
            ReconcileCursor(
                source=JobKind.SOURCES_SCAN,
                pass_queued_at=utcnow() - timedelta(seconds=5),
                pass_priority=WorkPriority.BACKFILL,
            )
        )
        db_session.add(ReconcileCursor(source=JobKind.STORAGE_MIGRATE))
        db_session.commit()

        assert forget_queued_passes() == 1

        db_session.expire_all()
        assert all(
            (row.pass_queued_at, row.pass_priority) == (None, None)
            for row in db_session.exec(select(ReconcileCursor)).all()
        )

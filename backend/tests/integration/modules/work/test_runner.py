"""One Job attempt's body: identical under every engine.

``execute_job`` re-reads the Job, declines an attempt that is superseded or a
Job already settled, runs the steps in order, and records the outcome exactly
once. A step of a mutating definition is admitted like any write: while a
restore holds the gate it is deferred (waited on durably), not failed. A Job
cancelled mid-run stops before its next step. Every completion nudges its own
definition and the sources it names, which is what keeps level-triggered work
flowing without a tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from sqlmodel import Session

from app.db.models import Job, JobKind, JobState, LaneName, WorkPriority
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.modules.work.contracts import (
    Deduplicated,
    EngineStatus,
    JobDefinition,
    JobOutcome,
    JobSubmission,
    Lane,
    PassSubmission,
    Step,
)
from app.modules.work.jobs import jobs
from app.modules.work.runner import execute_job
from app.runtime.engine.inline import InlineJobEngine, _Execution, _Runner

# Probes borrow real kinds and a lane; this catalog holds only them.
MUTATING = JobKind.INGESTION_UPLOAD
READ_ONLY = JobKind.SOURCES_SCAN
LANE = LaneName.INGEST


@dataclass
class Trace:
    steps: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    behaviour: dict[str, object] = field(default_factory=dict)


TRACE = Trace()


def _step(name: str):
    def run(ctx):
        TRACE.steps.append(name)
        behaviour = TRACE.behaviour.get(name)
        if callable(behaviour):
            return behaviour(ctx)
        return None

    return run


def _failed(_session, subject: str, reason: str) -> None:
    TRACE.failures.append((subject, reason))


@pytest.fixture
def engine() -> InlineJobEngine:
    TRACE.steps.clear()
    TRACE.failures.clear()
    TRACE.behaviour.clear()
    catalog = WorkCatalog(
        [
            JobDefinition(
                name=MUTATING,
                lane=LANE,
                steps=(Step("one", _step("one")), Step("two", _step("two"))),
                on_failure=_failed,
                completion_nudges=(READ_ONLY,),
                label="Mutating probe",
            ),
            JobDefinition(
                name=READ_ONLY,
                lane=LANE,
                steps=(Step("look", _step("look")),),
                mutating=False,
                label="Read-only probe",
            ),
        ],
        lanes={**default_lanes(), LANE: Lane(LANE, 2)},
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built


def _run(engine: InlineJobEngine, job: Job, attempt: int = 1) -> None:
    """Run one attempt's body directly, as an engine would."""
    execution = _Execution(
        submission=JobSubmission(
            execution_id=f"{job.id}:{attempt}",
            job_id=job.id,
            definition=job.kind,
            subject_key=job.subject_key,
            lane=LANE,
            priority=WorkPriority.INTERACTIVE,
            attempt=attempt,
            routing=Deduplicated(f"{job.kind}|{job.subject_key}"),
            execution_epoch=job.execution_epoch,
        ),
        sequence=0,
        status=EngineStatus.RUNNING,
        app_version=engine.app_version,
        executor_id=engine.executor_id,
    )
    execute_job(
        job.id, attempt, _Runner(engine, execution), execution_epoch=job.execution_epoch
    )


def _status(job_id: str):
    status = jobs.get(job_id)
    assert status is not None
    return status


def _nudged(engine: InlineJobEngine) -> set[JobKind]:
    return {
        ex.submission.source
        for ex in engine.executions.values()
        if isinstance(ex.submission, PassSubmission)
    }


class TestExecuteJob:
    def test_runs_every_step_in_order_to_completion(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)

        _run(engine, job)

        assert TRACE.steps == ["one", "two"]
        status = _status(job.id)
        assert (status.state, status.attempts) == ("completed", 1)
        assert status.started_at is not None

    def test_nudges_every_source_its_completion_affects(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)

        _run(engine, job)

        assert {MUTATING, READ_ONLY} <= _nudged(engine)

    def test_a_superseded_attempt_does_nothing(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        # Attempt 2 exists; a late attempt 1 must not run the steps again.
        job = make_job(kind=MUTATING, attempts=2)

        _run(engine, job, attempt=1)

        assert TRACE.steps == []
        assert _status(job.id).state == "queued"

    def test_a_settled_job_is_not_run_again(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.COMPLETED)

        _run(engine, job)

        assert TRACE.steps == []

    def test_a_job_that_no_longer_exists_does_nothing(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=MUTATING)
        db_session.delete(db_session.get(Job, job.id))
        db_session.commit()

        _run(engine, job)

        assert TRACE.steps == []

    def test_a_job_cancelled_mid_run_stops_before_its_next_step(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)
        TRACE.behaviour["one"] = lambda ctx: jobs.finish(
            ctx.job_id, JobOutcome.CANCELLED
        )

        _run(engine, job)

        assert TRACE.steps == ["one"]
        assert _status(job.id).state == "cancelled"

    def test_a_failing_step_fails_the_job_with_a_display_safe_reason(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)

        def broken(_ctx):
            raise OSError("cannot read /srv/secret/part.stl")

        TRACE.behaviour["one"] = broken

        _run(engine, job)

        status = _status(job.id)
        assert (status.state, status.retryable) == ("failed", True)
        assert status.error and "/srv/secret" not in status.error
        assert TRACE.steps == ["one"]

    def test_a_failed_job_withdraws_its_subject(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)
        TRACE.behaviour["one"] = lambda _ctx: (_ for _ in ()).throw(ValueError("bad"))

        _run(engine, job)

        assert TRACE.failures == [(job.subject_key, "bad")]

    def test_an_outcome_a_step_recorded_is_kept(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        # A step that knows better (a refused archive) finishes its own Job;
        # the runner never overwrites that verdict with "completed".
        job = make_job(kind=MUTATING)
        TRACE.behaviour["one"] = lambda ctx: ctx.finish(
            JobOutcome.FAILED, error="refused"
        )

        _run(engine, job)

        assert (_status(job.id).state, _status(job.id).error) == ("failed", "refused")

    def test_a_step_reports_progress_through_its_context(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)
        TRACE.behaviour["one"] = lambda ctx: ctx.update(stage="hashing", total=4)

        _run(engine, job)

        status = _status(job.id)
        assert (status.stage, status.total) == ("completed", 4)

    def test_a_step_waits_out_a_restore_instead_of_failing(
        self, engine: InlineJobEngine, make_job, monkeypatch
    ) -> None:
        from app.runtime import maintenance

        refusals = [False, False]
        monkeypatch.setattr(
            maintenance,
            "begin_mutating_operation",
            lambda: refusals.pop(0) if refusals else True,
        )
        monkeypatch.setattr(maintenance, "end_mutating_operation", lambda: None)
        job = make_job(kind=MUTATING)
        before = engine.clock

        _run(engine, job)

        assert TRACE.steps == ["one", "two"]
        assert _status(job.id).state == "completed"
        # Durable waits of 1s then 2s, not a failure.
        assert engine.clock - before == pytest.approx(3.0)

    def test_a_mutating_step_counts_as_a_write_while_it_runs(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        # A restore drains every write before it replaces the database.
        from app.runtime import maintenance

        seen: list[int] = []
        TRACE.behaviour["one"] = lambda _ctx: seen.append(
            maintenance.active_mutations()
        )
        job = make_job(kind=MUTATING)

        _run(engine, job)

        assert seen == [1]
        assert maintenance.active_mutations() == 0

    def test_a_failing_step_releases_its_write(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        # A leaked write would hold every future restore waiting to drain.
        from app.runtime import maintenance

        TRACE.behaviour["one"] = lambda _ctx: (_ for _ in ()).throw(ValueError("bad"))
        job = make_job(kind=MUTATING)

        _run(engine, job)

        assert _status(job.id).state == "failed"
        assert maintenance.active_mutations() == 0

    def test_a_read_only_step_is_not_a_write(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        from app.runtime import maintenance

        seen: list[int] = []
        TRACE.behaviour["look"] = lambda _ctx: seen.append(
            maintenance.active_mutations()
        )
        job = make_job(kind=READ_ONLY)

        _run(engine, job)

        assert seen == [0]

    def test_a_read_only_definition_is_not_held_by_a_restore(
        self, engine: InlineJobEngine, make_job, monkeypatch
    ) -> None:
        from app.runtime import maintenance

        monkeypatch.setattr(maintenance, "begin_mutating_operation", lambda: False)
        job = make_job(kind=READ_ONLY)

        _run(engine, job)

        assert TRACE.steps == ["look"]

    def test_an_engine_cancellation_unwinds_without_settling(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        from app.runtime.engine.inline import InlineCancelled

        job = make_job(kind=MUTATING)

        def cancelled(_ctx):
            raise InlineCancelled(job.id)

        TRACE.behaviour["one"] = cancelled

        with pytest.raises(InlineCancelled):
            _run(engine, job)

        assert _status(job.id).state == "running"

    def test_an_interrupt_is_never_swallowed(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING)

        def interrupted(_ctx):
            raise KeyboardInterrupt

        TRACE.behaviour["one"] = interrupted

        with pytest.raises(KeyboardInterrupt):
            _run(engine, job)


class TestActiveAttempt:
    def test_superseded_execution_is_withdrawn(self, make_job):
        from app.modules.work.runner import _withdrawn

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            state=JobState.RUNNING,
            attempts=2,
        )
        assert _withdrawn(job.id, 1, job.execution_epoch)
        assert not _withdrawn(job.id, 2, job.execution_epoch)

    def test_requeued_execution_is_withdrawn(self, make_job):
        from app.modules.work.runner import _withdrawn

        job = make_job(kind=JobKind.DERIVATIVES_MESH, attempts=1)
        assert _withdrawn(job.id, 1, job.execution_epoch)
        assert not _withdrawn(job.id)

    @pytest.mark.parametrize(
        "state,attempts",
        [(JobState.QUEUED, 1), (JobState.RUNNING, 2)],
        ids=["requeued", "superseded"],
    )
    def test_superseded_step_cannot_write(self, make_job, tmp_path, state, attempts):
        from app.modules.work.contracts import Step
        from app.modules.work.runner import ExecutionContext, StepOutcome, _run_step

        job = make_job(kind=JobKind.DERIVATIVES_MESH, state=state, attempts=attempts)
        output = tmp_path / "stale-output"
        context = ExecutionContext(
            job_id=job.id,
            definition=job.kind,
            subject_key=job.subject_key,
            priority=job.priority,
            execution_id=f"{job.id}:1",
            attempt=1,
            execution_epoch=job.execution_epoch,
        )
        outcome = _run_step(
            Step("native", lambda _ctx: output.write_bytes(b"stale")),
            context,
            mutating=False,
        )
        assert outcome == StepOutcome.CANCELLED.value
        assert not output.exists()


class TestAttemptSettlement:
    def test_late_failure_preserves_the_retried_derivative(
        self, db_session, make_job, make_model, make_file, make_derivative
    ):
        from app.core.time import utcnow
        from app.db.models import DerivativeKind, DerivativeState
        from app.modules.derivatives import records
        from app.modules.derivatives.jobs import definitions
        from app.modules.derivatives.kinds import recipes_for
        from app.modules.work.runner import _settle

        artifact = make_file(make_model(), filename="retry.stl")
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{artifact.id}",
            state=JobState.RUNNING,
            attempts=1,
        )
        definition = next(
            d for d in definitions() if d.name == JobKind.DERIVATIVES_MESH
        )
        recipe = recipes_for(artifact)[DerivativeKind.THUMBNAIL]
        records.begin(
            db_session, artifact, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        db_session.commit()
        records.cancel(
            db_session, artifact, {DerivativeKind.THUMBNAIL: recipe}, now=utcnow()
        )
        db_session.commit()
        records.reset(db_session, artifact)
        db_session.commit()
        new = records.begin(
            db_session, artifact, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
        )
        token = new.attempt_token
        job.state = JobState.RUNNING
        job.attempts = 2
        db_session.add(job)
        db_session.commit()

        _settle(
            job.id,
            1,
            job.execution_epoch,
            definition,
            job.subject_key,
            "late_old_worker_error",
        )

        db_session.expire_all()
        assert db_session.get(Job, job.id).state is JobState.RUNNING
        current = records.rows_for(db_session, artifact)[DerivativeKind.THUMBNAIL]
        assert (current.state, current.attempt_token, current.failure_reason) == (
            DerivativeState.RUNNING,
            token,
            None,
        )

    def test_old_context_cannot_finish_a_new_attempt(self, db_session, make_job):
        from app.modules.work.runner import ExecutionContext

        job = make_job(
            kind=MUTATING, subject="ingest/progress", state=JobState.RUNNING, attempts=2
        )
        context = ExecutionContext(
            job.id,
            job.kind,
            job.subject_key,
            job.priority,
            f"{job.id}:1",
            1,
            execution_epoch=job.execution_epoch,
        )

        context.finish(JobOutcome.FAILED, error="late_old_error")

        db_session.expire_all()
        assert db_session.get(Job, job.id).state is JobState.RUNNING

    def test_old_context_cannot_report_progress_for_a_new_attempt(
        self, db_session, make_job
    ):
        from app.modules.work.runner import ExecutionContext

        job = make_job(
            kind=MUTATING, subject="ingest/progress", state=JobState.RUNNING, attempts=2
        )
        context = ExecutionContext(
            job.id,
            job.kind,
            job.subject_key,
            job.priority,
            f"{job.id}:1",
            1,
            execution_epoch=job.execution_epoch,
        )

        context.update(processed=50)

        assert jobs.get(job.id).processed == 0

    def test_retried_job_with_old_attempt_number_cannot_be_reclaimed(
        self, db_session, make_job
    ):
        from app.modules.work.runner import _begin

        job = make_job(
            kind=MUTATING, subject="ingest/retry", state=JobState.QUEUED, attempts=1
        )

        assert _begin(job.id, 1, "previous-epoch") is None

        db_session.expire_all()
        assert db_session.get(Job, job.id).state is JobState.QUEUED


class TestEpochCallbacks:
    def test_legacy_callback_without_epoch_cannot_begin(self, engine, make_job):
        job = make_job(kind=MUTATING)

        # Legacy durable engine arguments contain only Job id and attempt.
        class UnexpectedRunner:
            def run(self, *_args):
                raise AssertionError("legacy callback must not execute checkpoints")

        execute_job(job.id, 1, UnexpectedRunner())
        assert _status(job.id).state is JobState.QUEUED
        assert _status(job.id).attempts == 0

    def test_old_epoch_cannot_write_to_same_attempt_number(
        self, db_session, engine, make_job
    ):
        from app.modules.work.runner import ExecutionContext, _settle

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        old_epoch = job.execution_epoch
        context = ExecutionContext(
            job.id,
            job.kind,
            job.subject_key,
            job.priority,
            f"{job.id}:{old_epoch}:1",
            1,
            old_epoch,
        )
        job.execution_epoch = "retried-epoch"
        job.submitted_epoch = job.execution_epoch
        db_session.add(job)
        db_session.commit()

        context.update(processed=99)
        context.finish(JobOutcome.FAILED, error="late callback")
        _settle(
            job.id,
            1,
            old_epoch,
            catalog_module.get_catalog().definition(MUTATING),
            job.subject_key,
            "late error",
        )

        assert _status(job.id).state is JobState.RUNNING
        assert _status(job.id).processed == 0
        assert TRACE.failures == []


class TestStepPriority:
    @pytest.mark.parametrize(
        "priority", [WorkPriority.INTERACTIVE, WorkPriority.BACKFILL]
    )
    def test_binds_step_work_priority(self, engine, make_job, priority):
        from app.core.work_priority import current_priority

        observed = []
        TRACE.behaviour["one"] = lambda _context: observed.append(current_priority())
        TRACE.behaviour["two"] = lambda _context: observed.append(current_priority())
        job = make_job(kind=MUTATING, priority=priority)
        _run(engine, job)
        assert observed == [priority, priority]
        assert current_priority() is WorkPriority.INTERACTIVE

    def test_restores_priority_after_failed_step(self, engine, make_job):
        from app.core.work_priority import current_priority

        observed = []

        def failure(_context):
            observed.append(current_priority())
            raise RuntimeError("priority probe failed")

        TRACE.behaviour["one"] = failure
        job = make_job(kind=MUTATING, priority=WorkPriority.BACKFILL)
        _run(engine, job)
        assert observed == [WorkPriority.BACKFILL]
        assert current_priority() is WorkPriority.INTERACTIVE
        assert _status(job.id).state is JobState.FAILED

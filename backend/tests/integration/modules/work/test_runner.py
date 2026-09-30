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
        ),
        sequence=0,
        status=EngineStatus.RUNNING,
        app_version=engine.app_version,
        executor_id=engine.executor_id,
    )
    execute_job(job.id, attempt, _Runner(engine, execution))


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
        assert _withdrawn(job.id, 1)
        assert not _withdrawn(job.id, 2)

    def test_requeued_execution_is_withdrawn(self, make_job):
        from app.modules.work.runner import _withdrawn

        job = make_job(kind=JobKind.DERIVATIVES_MESH, attempts=1)
        assert _withdrawn(job.id, 1)
        assert not _withdrawn(job.id)

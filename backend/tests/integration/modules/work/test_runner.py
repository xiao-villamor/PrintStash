"""One Job attempt's body: identical under every engine.

``execute_job`` re-reads the Job, declines an attempt that is superseded or a
Job already settled, runs the steps in order, and records the outcome exactly
once. A step of a mutating definition is admitted like any write: while a
restore holds the gate it is deferred (waited on durably), not failed. A Job
cancelled mid-run stops before its next step. Every completion nudges its own
definition and the sources it names, which is what keeps level-triggered work
flowing without a tick.

An attempt's end is one write: the terminal state, the subject's failure hook,
the completion stamps and, when the step asks for it, the settled cleanup.
Progress a short attempt reports is held and written with that same write.
When this goes red an attempt either pays for transactions nobody reads or
loses what it reported.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import (
    Job,
    JobKind,
    JobState,
    LaneName,
    ReconcileCursor,
    WorkPriority,
)
from app.db.session import get_session_factory
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
from app.modules.work.runner import ExecutionContext, _settle, execute_job
from app.runtime.engine.inline import InlineJobEngine, _Execution, _Runner

# Probes borrow real kinds and a lane; this catalog holds only them.
MUTATING = JobKind.INGESTION_UPLOAD
READ_ONLY = JobKind.SOURCES_SCAN
DISCOVERED = JobKind.DERIVATIVES_MESH


class _NothingPending:
    def pending(self, session, *, now, limit):
        return []

    def next_due(self, session, *, now):
        return None


LANE = LaneName.INGEST


@dataclass
class Trace:
    steps: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    settled: list[str] = field(default_factory=list)
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


def _cleanup(session, subject: str) -> None:
    TRACE.settled.append(subject)
    behaviour = TRACE.behaviour.get("settled")
    if callable(behaviour):
        behaviour(session, subject)


@pytest.fixture
def engine() -> InlineJobEngine:
    TRACE.steps.clear()
    TRACE.failures.clear()
    TRACE.settled.clear()
    TRACE.behaviour.clear()
    catalog = WorkCatalog(
        [
            JobDefinition(
                name=MUTATING,
                lane=LANE,
                steps=(Step("one", _step("one")), Step("two", _step("two"))),
                on_failure=_failed,
                on_settled=_cleanup,
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
            JobDefinition(
                name=DISCOVERED,
                lane=LANE,
                steps=(Step("look", _step("look")),),
                source=_NothingPending(),
                label="Discovered probe",
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


def _stamped(session: Session) -> set[JobKind]:
    """The sources whose dirty mark is set."""
    return {
        row.source
        for row in session.exec(select(ReconcileCursor)).all()
        if row.nudged_at is not None
    }


def _context(job: Job, *, age: float = 0.0) -> ExecutionContext:
    """The context of ``job``'s first attempt, begun ``age`` seconds ago."""
    return ExecutionContext(
        job_id=job.id,
        definition=job.kind,
        subject_key=job.subject_key,
        priority=job.priority,
        execution_id=f"{job.id}:{job.execution_epoch}:1",
        attempt=1,
        execution_epoch=job.execution_epoch,
        started=time.monotonic() - age,
    )


def _wait_for(reached, *, seconds: float = 5.0) -> None:
    """Give a background write up to ``seconds`` to land."""
    deadline = time.monotonic() + seconds
    while not reached() and time.monotonic() < deadline:
        time.sleep(0.02)


@pytest.fixture
def writes(db_session: Session) -> list[str]:
    """Every transaction the application database begins as a writer."""
    seen: list[str] = []

    def record(_conn, _cursor, statement, *_rest) -> None:
        if statement.startswith("BEGIN IMMEDIATE"):
            seen.append(statement)

    target = db_session.get_bind()
    event.listen(target, "before_cursor_execute", record)
    yield seen
    event.remove(target, "before_cursor_execute", record)


@pytest.fixture
def hold() -> float:
    _overlay["jobs_progress_hold_seconds"] = 30.0
    return 30.0


class TestExecutionContext:
    def test_holds_a_report_made_before_the_attempt_is_watched(
        self, engine: InlineJobEngine, make_job, hold: float, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)

        context.update(stage="hashing", total=4)

        assert (_status(job.id).stage, _status(job.id).total, writes) == (
            None,
            None,
            [],
        )

    def test_writes_a_report_made_once_the_attempt_is_watched(
        self, engine: InlineJobEngine, make_job, hold: float
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job, age=hold)

        context.update(stage="hashing", total=4)

        assert (_status(job.id).stage, _status(job.id).total) == ("hashing", 4)

    def test_writes_held_reports_with_the_next_one_in_one_transaction(
        self, engine: InlineJobEngine, make_job, hold: float, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.update(current_item="plate.stl", processed=1)
        context.update(processed=2)
        context.started -= hold

        context.update(total=8)

        status = _status(job.id)
        assert (status.current_item, status.processed, status.total, len(writes)) == (
            "plate.stl",
            2,
            8,
            1,
        )

    def test_a_later_held_report_overrides_an_earlier_one(
        self, engine: InlineJobEngine, make_job, hold: float
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.update(current_item="first.stl")
        context.update(current_item="second.stl")

        context.finish(JobOutcome.COMPLETED)

        assert _status(job.id).current_item == "second.stl"

    def test_held_progress_is_written_when_the_hold_ends_without_another_report(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        # A step that reports once and then blocks (a slow download) never
        # calls back: what it reported must still appear.
        _overlay["jobs_progress_hold_seconds"] = 0.2
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)

        context.update(current_item="first.stl", processed=1)

        _wait_for(lambda: _status(job.id).processed == 1)
        assert (_status(job.id).processed, _status(job.id).current_item) == (
            1,
            "first.stl",
        )

    def test_an_attempt_that_ended_inside_its_hold_is_not_written_again(
        self, engine: InlineJobEngine, make_job, writes: list[str]
    ) -> None:
        _overlay["jobs_progress_hold_seconds"] = 0.2
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.update(processed=1)
        context.settle(JobOutcome.COMPLETED)
        writes.clear()

        time.sleep(0.5)

        assert writes == []

    def test_flush_due_writes_held_reports_once_the_attempt_is_watched(
        self, engine: InlineJobEngine, make_job, hold: float
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.update(stage="hashing", total=4)
        context.started -= hold

        context.flush_due()

        assert (_status(job.id).stage, _status(job.id).total) == ("hashing", 4)

    def test_flush_due_keeps_holding_inside_the_hold(
        self, engine: InlineJobEngine, make_job, hold: float, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.update(stage="hashing")

        context.flush_due()

        assert (_status(job.id).stage, writes) == (None, [])

    def test_flush_due_with_nothing_held_writes_nothing(
        self, engine: InlineJobEngine, make_job, hold: float, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job, age=hold)

        context.flush_due()

        assert writes == []

    def test_finish_carries_held_reports_into_the_terminal_status(
        self, engine: InlineJobEngine, make_job, hold: float, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.update(current_item="plate.stl", processed=3, total=8)

        context.finish(JobOutcome.COMPLETED)

        status = _status(job.id)
        assert (status.state, status.current_item, status.processed, len(writes)) == (
            "completed",
            "plate.stl",
            3,
            1,
        )

    def test_finish_stamps_the_sources_its_completion_names(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        assert (_stamped(db_session), _nudged(engine)) == ({READ_ONLY}, {READ_ONLY})

    @pytest.mark.parametrize(
        "waiting",
        [
            {"state": JobState.QUEUED},
            {"state": JobState.INTERRUPTED, "attempts": 1},
            {"state": JobState.QUEUED, "attempts": 1, "submitted_epoch": "earlier"},
        ],
        ids=["never-submitted", "interrupted", "epoch-not-submitted"],
    )
    def test_finish_stamps_its_own_source_while_a_job_awaits_submission(
        self, engine: InlineJobEngine, make_job, db_session: Session, waiting: dict
    ) -> None:
        make_job(kind=MUTATING, **waiting)
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        assert MUTATING in _stamped(db_session) & _nudged(engine)

    @pytest.mark.parametrize(
        "other",
        [
            {"state": JobState.QUEUED, "attempts": 1},
            {"state": JobState.RUNNING, "attempts": 1},
            {"state": JobState.COMPLETED},
        ],
        ids=["submitted", "running", "settled"],
    )
    def test_finish_queues_no_pass_over_request_work_with_nothing_to_submit(
        self, engine: InlineJobEngine, make_job, db_session: Session, other: dict
    ) -> None:
        make_job(kind=MUTATING, **other)
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        assert MUTATING not in _stamped(db_session) | _nudged(engine)

    def test_finish_stamps_the_source_of_discovered_work(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=DISCOVERED, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        assert DISCOVERED in _stamped(db_session) & _nudged(engine)

    def test_finish_leaves_the_settled_cleanup_to_the_end_of_the_attempt(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)

        context.finish(JobOutcome.COMPLETED)

        assert (TRACE.settled, context.ended, context.cleaned) == ([], True, False)

    def test_settle_runs_the_settled_cleanup_in_the_terminal_write(
        self, engine: InlineJobEngine, make_job, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)

        context.settle(JobOutcome.COMPLETED)

        assert (_status(job.id).state, TRACE.settled, context.cleaned, len(writes)) == (
            "completed",
            [job.subject_key],
            True,
            1,
        )

    def test_settle_keeps_the_job_terminal_when_its_cleanup_fails(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        TRACE.behaviour["settled"] = lambda *_: (_ for _ in ()).throw(OSError("busy"))

        context.settle(JobOutcome.COMPLETED)

        assert (_status(job.id).state, context.ended, context.cleaned) == (
            "completed",
            True,
            False,
        )

    def test_a_definition_without_cleanup_is_clean_once_finished(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=READ_ONLY, state=JobState.RUNNING, attempts=1)
        context = _context(job)

        context.finish(JobOutcome.COMPLETED)

        assert (context.ended, context.cleaned) == (True, True)

    def test_a_superseded_context_ends_nothing(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=2)
        context = _context(job)

        context.settle(JobOutcome.COMPLETED)

        assert (_status(job.id).state, TRACE.settled, context.ended) == (
            "running",
            [],
            False,
        )


class TestSettle:
    def test_an_attempt_that_settled_itself_costs_no_further_transaction(
        self, engine: InlineJobEngine, make_job, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.settle(JobOutcome.COMPLETED)
        writes.clear()

        _settle(context, None)

        assert (writes, TRACE.settled) == ([], [job.subject_key])

    def test_runs_the_cleanup_a_finished_step_left_in_one_transaction(
        self, engine: InlineJobEngine, make_job, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.finish(JobOutcome.COMPLETED)
        writes.clear()

        _settle(context, None)

        assert (TRACE.settled, len(writes)) == ([job.subject_key], 1)

    def test_a_finished_job_without_cleanup_costs_no_transaction(
        self, engine: InlineJobEngine, make_job, writes: list[str]
    ) -> None:
        job = make_job(kind=READ_ONLY, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        context.finish(JobOutcome.COMPLETED)
        writes.clear()

        _settle(context, None)

        assert writes == []

    def test_completes_a_job_no_step_finished_in_one_transaction(
        self, engine: InlineJobEngine, make_job, writes: list[str]
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)

        _settle(_context(job), None)

        assert (_status(job.id).state, TRACE.settled, len(writes)) == (
            "completed",
            [job.subject_key],
            1,
        )

    def test_retries_a_cleanup_that_failed_with_the_terminal_write(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        context = _context(job)
        TRACE.behaviour["settled"] = lambda *_: (_ for _ in ()).throw(OSError("busy"))
        context.settle(JobOutcome.COMPLETED)
        TRACE.behaviour.clear()
        TRACE.settled.clear()

        _settle(context, None)

        assert (TRACE.settled, context.cleaned) == ([job.subject_key], True)

    def test_a_recovered_execution_stamps_again_for_a_job_already_ended(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        # It cannot know whether the execution that ended the Job lived long
        # enough to queue its passes, so it marks the sources dirty once more.
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        _context(job).finish(JobOutcome.COMPLETED)
        first = db_session.get(ReconcileCursor, READ_ONLY).nudged_at
        db_session.expire_all()

        _settle(_context(job), None)

        assert db_session.get(ReconcileCursor, READ_ONLY).nudged_at > first

    def test_a_failure_recorded_at_the_end_reaches_the_failure_hook(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)

        _settle(_context(job), "exploded")

        assert (_status(job.id).state, TRACE.failures) == (
            "failed",
            [(job.subject_key, "exploded")],
        )


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

        assert READ_ONLY in _nudged(engine)

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

    def test_a_withdrawn_attempt_ends_instead_of_waiting_out_a_restore(
        self, engine: InlineJobEngine, make_job, monkeypatch
    ) -> None:
        from app.runtime import maintenance

        job = make_job(kind=MUTATING)

        def cancel_then_refuse() -> bool:
            with get_session_factory().scoped_session() as session:
                row = session.get(Job, job.id)
                assert row is not None
                row.state = JobState.CANCELLED
                session.add(row)
                session.commit()
            return False

        monkeypatch.setattr(maintenance, "begin_mutating_operation", cancel_then_refuse)
        before = engine.clock

        _run(engine, job)

        assert (TRACE.steps, engine.clock - before) == ([], 0)

    def test_claiming_an_attempt_is_one_transaction(
        self, engine: InlineJobEngine, make_job, transactions: list[str]
    ) -> None:
        from app.modules.work.runner import _begin

        job = make_job(kind=MUTATING)
        transactions.clear()

        claim = _begin(job.id, 1, job.execution_epoch)

        assert (claim, len(transactions)) == (
            {
                "kind": MUTATING.value,
                "subject_key": job.subject_key,
                "priority": job.priority.value,
            },
            1,
        )

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


class TestWithdrawalWatch:
    """Waiting steps share one read; see ``runner._WithdrawalWatch``."""

    @pytest.fixture
    def reads(self, db_session: Session) -> list[str]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, *_rest) -> None:
            if "FROM jobs" in statement:
                seen.append(statement)

        target = db_session.get_bind()
        event.listen(target, "before_cursor_execute", record)
        yield seen
        event.remove(target, "before_cursor_execute", record)

    def test_one_read_answers_every_registered_attempt(
        self, make_job, reads: list[str]
    ) -> None:
        from app.modules.work.runner import _WithdrawalWatch

        running = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        cancelled = make_job(kind=MUTATING, state=JobState.CANCELLED, attempts=1)
        watch = _WithdrawalWatch()
        watch.add(running.id, 1, running.execution_epoch)
        watch.add(cancelled.id, 1, cancelled.execution_epoch)
        reads.clear()

        answers = (
            watch.withdrawn(running.id, 1, running.execution_epoch),
            watch.withdrawn(cancelled.id, 1, cancelled.execution_epoch),
        )

        assert (answers, len(reads)) == ((False, True), 1)

    def test_the_read_runs_without_the_lock_other_steps_wait_on(
        self, make_job, monkeypatch
    ) -> None:
        from app.modules.work.runner import _WithdrawalWatch

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        watch = _WithdrawalWatch()
        watch.add(job.id, 1, job.execution_epoch)
        read = watch._read
        locked: list[bool] = []

        def observed(job_ids):
            locked.append(watch._lock.locked())
            return read(job_ids)

        monkeypatch.setattr(watch, "_read", observed)

        watch.withdrawn(job.id, 1, job.execution_epoch)

        assert locked == [False]

    def test_a_probe_during_another_steps_read_takes_the_last_answer(
        self, make_job, monkeypatch
    ) -> None:
        from app.modules.work import runner

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        identity = (job.id, 1, job.execution_epoch)
        clock = [100.0]
        monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
        watch = runner._WithdrawalWatch()
        watch.add(*identity)
        watch.withdrawn(*identity)
        clock[0] += runner._WATCH_SECONDS
        read = watch._read
        during: list[bool] = []

        def observed(job_ids):
            during.append(watch.withdrawn(*identity))
            return read(job_ids)

        monkeypatch.setattr(watch, "_read", observed)

        watch.withdrawn(*identity)

        assert during == [False]

    def test_a_failed_read_lets_the_next_probe_read(
        self, make_job, monkeypatch
    ) -> None:
        from app.modules.work.runner import _WithdrawalWatch

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        identity = (job.id, 1, job.execution_epoch)
        watch = _WithdrawalWatch()
        watch.add(*identity)
        read = watch._read

        def broken(_job_ids):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(watch, "_read", broken)
        with pytest.raises(RuntimeError, match="database unavailable"):
            watch.withdrawn(*identity)
        monkeypatch.setattr(watch, "_read", read)

        assert watch.withdrawn(*identity) is False

    def test_an_answer_older_than_the_window_is_read_again(
        self, make_job, db_session: Session, monkeypatch
    ) -> None:
        from app.modules.work import runner

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        clock = [100.0]
        monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
        watch = runner._WithdrawalWatch()
        watch.add(job.id, 1, job.execution_epoch)
        before = watch.withdrawn(job.id, 1, job.execution_epoch)
        job.state = JobState.CANCELLED
        db_session.add(job)
        db_session.commit()

        inside = watch.withdrawn(job.id, 1, job.execution_epoch)
        clock[0] += runner._WATCH_SECONDS
        after = watch.withdrawn(job.id, 1, job.execution_epoch)

        assert (before, inside, after) == (False, False, True)

    @pytest.mark.parametrize(
        "change",
        [{"attempts": 2}, {"execution_epoch": "replaced"}, {"state": JobState.QUEUED}],
        ids=["superseded", "epoch-replaced", "requeued"],
    )
    def test_an_attempt_that_lost_the_job_is_withdrawn(
        self, make_job, db_session: Session, change: dict
    ) -> None:
        from app.modules.work.runner import _WithdrawalWatch

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        epoch = job.execution_epoch
        for name, value in change.items():
            setattr(job, name, value)
        db_session.add(job)
        db_session.commit()
        watch = _WithdrawalWatch()
        watch.add(job.id, 1, epoch)

        assert watch.withdrawn(job.id, 1, epoch)

    def test_a_deleted_job_is_withdrawn(self, make_job, db_session: Session) -> None:
        from app.modules.work.runner import _WithdrawalWatch

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=1)
        identity = (job.id, 1, job.execution_epoch)
        db_session.delete(job)
        db_session.commit()
        watch = _WithdrawalWatch()
        watch.add(*identity)

        assert watch.withdrawn(*identity)

    def test_a_retried_attempt_keeps_its_own_answer_when_the_old_one_leaves(
        self, make_job
    ) -> None:
        from app.modules.work.runner import _WithdrawalWatch

        job = make_job(kind=MUTATING, state=JobState.RUNNING, attempts=2)
        watch = _WithdrawalWatch()
        watch.add(job.id, 1, job.execution_epoch)
        watch.add(job.id, 2, job.execution_epoch)
        watch.remove(job.id, 1, job.execution_epoch)

        assert not watch.withdrawn(job.id, 2, job.execution_epoch)

    def test_a_steps_paced_checkpoints_share_one_read(
        self, engine: InlineJobEngine, make_job, reads: list[str]
    ) -> None:
        from app.core.cancellation import checkpoint

        job = make_job(kind=MUTATING)
        seen: list[int] = []

        def waits(_ctx) -> None:
            reads.clear()
            checkpoint()
            checkpoint()
            seen.append(len(reads))

        TRACE.behaviour["one"] = waits

        _run(engine, job)

        assert seen == [1]

    def test_a_steps_forced_checkpoint_reads_its_job_afresh(
        self, engine: InlineJobEngine, make_job, reads: list[str]
    ) -> None:
        from app.core.cancellation import checkpoint

        job = make_job(kind=MUTATING)
        seen: list[int] = []

        def waits(_ctx) -> None:
            checkpoint()
            reads.clear()
            checkpoint(force=True)
            seen.append(len(reads))

        TRACE.behaviour["one"] = waits

        _run(engine, job)

        assert seen == [1]

    def test_a_step_cancelled_while_it_waits_stops(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        from app.core.cancellation import checkpoint

        job = make_job(kind=MUTATING)

        def cancelled_while_waiting(_ctx) -> None:
            with get_session_factory().scoped_session() as session:
                row = session.get(Job, job.id)
                assert row is not None
                row.state = JobState.CANCELLED
                session.add(row)
                session.commit()
            checkpoint()

        TRACE.behaviour["one"] = cancelled_while_waiting

        _run(engine, job)

        assert (TRACE.steps, _status(job.id).state) == (["one"], "cancelled")

    def test_a_finished_step_leaves_the_watch(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        from app.modules.work import runner

        job = make_job(kind=MUTATING)

        _run(engine, job)

        assert runner._watch._attempts == {}


class TestActiveAttempt:
    def test_withdrawal_reads_current_authority_without_cached_results(self, make_job):
        from app.db.session import get_session_factory
        from app.modules.work.runner import _withdrawn

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, state=JobState.RUNNING, attempts=1
        )
        assert not _withdrawn(job.id, 1, job.execution_epoch)
        assert _withdrawn(job.id, 1, "retired-epoch")
        with get_session_factory().scoped_session() as session:
            row = session.get(Job, job.id)
            assert row is not None
            row.state = JobState.CANCELLED
            session.add(row)
            session.commit()
        assert _withdrawn(job.id, 1, job.execution_epoch)
        assert _withdrawn(job.id)
        with get_session_factory().scoped_session() as session:
            row = session.get(Job, job.id)
            assert row is not None
            session.delete(row)
            session.commit()
        assert _withdrawn(job.id)

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
            ExecutionContext(
                job_id=job.id,
                definition=definition.name,
                subject_key=job.subject_key,
                priority=job.priority,
                execution_id=f"{job.id}:{job.execution_epoch}:1",
                attempt=1,
                execution_epoch=job.execution_epoch,
            ),
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
        _settle(context, "late error")

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


class TestAdditionsRelease:
    """The last addition to settle releases the work that waited for it."""

    def test_the_last_addition_to_end_nudges_the_waiting_work(
        self, additions_engine, make_job, db_session: Session
    ) -> None:
        from .conftest import ADDING, WAITING

        job = make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        cursor = db_session.get(ReconcileCursor, WAITING)
        assert cursor is not None and cursor.nudged_at is not None
        assert WAITING in _nudged(additions_engine)

    def test_an_addition_that_ends_before_others_leaves_it_waiting(
        self, additions_engine, make_job
    ) -> None:
        from .conftest import ADDING, ALSO_ADDING, WAITING

        make_job(kind=ALSO_ADDING, state=JobState.RUNNING, attempts=1)
        job = make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.COMPLETED)

        assert WAITING not in _nudged(additions_engine)

    def test_a_failed_addition_releases_the_waiting_work_too(
        self, additions_engine, make_job
    ) -> None:
        from .conftest import ADDING, WAITING

        job = make_job(kind=ADDING, state=JobState.RUNNING, attempts=1)

        _context(job).finish(JobOutcome.FAILED, error="broken_upload")

        assert WAITING in _nudged(additions_engine)

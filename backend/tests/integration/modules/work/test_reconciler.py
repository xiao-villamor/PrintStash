"""A reconcile pass against the real database: repair, discover, and bound.

``decide`` (unit-tested) says what a Job needs; this file defends what a pass
*does* about it, on real rows and a real engine: submitting first attempts,
interrupting and resubmitting lost ones, failing exhausted ones and calling
their failure hook, finishing ones whose terminal write was lost. Discovery
creates one Job per pending subject, never more than the batch or the lane's
headroom allows, and holds back a subject that keeps coming straight back.

A pass is single-flight per definition, re-runs itself when a nudge lands
while it runs, and asks for a delayed pass at its source's next due time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.time import utcnow
from app.db.models import (
    Job,
    JobKind,
    JobState,
    LaneName,
    ReconcileCursor,
    WorkPriority,
)
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.modules.work.contracts import (
    EngineStatus,
    JobDefinition,
    Lane,
    PassSubmission,
    SkipReason,
    Step,
    WorkItem,
)
from app.modules.work.reconciler import (
    run_pass,
    sweep_foreign_versions,
    sweep_lost_passes,
)
from app.modules.work.submission import execution_id, nudge
from app.runtime.engine.inline import InlineJobEngine

# Probe definitions borrow real kinds and a real lane: the catalog each test
# binds holds only these two, so nothing else answers to those names.
REQUESTED = JobKind.INGESTION_UPLOAD
SOURCED = JobKind.SOURCES_SCAN
PROBE_LANE = LaneName.MAINTENANCE


@dataclass
class Probe:
    items: list[WorkItem] = field(default_factory=list)
    due: datetime | None = None
    calls: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    on_pending: object | None = None


PROBE = Probe()


class _Source:
    def pending(self, session, *, now, limit):
        PROBE.calls += 1
        if callable(PROBE.on_pending):
            PROBE.on_pending()
        return PROBE.items[:limit]

    def next_due(self, session, *, now):
        return PROBE.due


def _noop(_ctx) -> None:
    return None


def _failed(_session, subject: str, reason: str) -> None:
    PROBE.failures.append((subject, reason))


@pytest.fixture
def engine() -> InlineJobEngine:
    PROBE.items = []
    PROBE.due = None
    PROBE.calls = 0
    PROBE.failures = []
    PROBE.on_pending = None
    lanes = {**default_lanes(), PROBE_LANE: Lane(PROBE_LANE, 1)}
    catalog = WorkCatalog(
        [
            JobDefinition(
                name=REQUESTED,
                lane=PROBE_LANE,
                steps=(Step(f"{REQUESTED}.run", _noop),),
                label="Requested probe",
                on_failure=_failed,
            ),
            JobDefinition(
                name=SOURCED,
                lane=PROBE_LANE,
                steps=(Step(f"{SOURCED}.run", _noop),),
                label="Sourced probe",
                source=_Source(),
            ),
        ],
        lanes=lanes,
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built


def _row(session: Session, job_id: str) -> Job:
    session.expire_all()
    row = session.get(Job, job_id)
    assert row is not None
    return row


def _jobs(session: Session, kind: JobKind = SOURCED) -> list[Job]:
    session.expire_all()
    return list(session.exec(select(Job).where(Job.kind == kind)).all())


def _items(*subjects: str, **kw) -> list[WorkItem]:
    return [WorkItem(subject_key=subject, **kw) for subject in subjects]


def _passes(engine: InlineJobEngine, source: JobKind) -> list:
    return [
        execution
        for execution in engine.executions.values()
        if isinstance(execution.submission, PassSubmission)
        and execution.submission.source == source
    ]


def _stale(make_job, *, attempts: int = 1, **kw) -> Job:
    """A running Job last touched past the submission grace."""
    return make_job(
        kind=REQUESTED,
        state=kw.pop("state", JobState.RUNNING),
        attempts=attempts,
        updated_at=utcnow() - timedelta(seconds=settings.jobs_submit_grace_seconds + 5),
        **kw,
    )


class TestRepair:
    def test_submits_the_first_attempt_of_a_queued_job(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)

        result = run_pass(REQUESTED)

        assert result.submitted == 1
        assert _row(db_session, job.id).attempts == 1
        assert execution_id(job.id, 1, job.execution_epoch) in engine.executions

    def test_resubmits_an_execution_the_engine_lost(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = _stale(make_job)

        result = run_pass(REQUESTED)

        row = _row(db_session, job.id)
        assert (result.interrupted, result.submitted) == (1, 1)
        assert (row.attempts, row.resubmits) == (2, 1)
        assert execution_id(job.id, 2, job.execution_epoch) in engine.executions

    def test_records_why_a_job_was_interrupted(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        import json

        job = _stale(make_job)

        run_pass(REQUESTED)

        assert json.loads(_row(db_session, job.id).status_json)["error"] == (
            "execution_lost"
        )

    def test_fails_a_job_lost_more_often_than_its_budget(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = _stale(make_job, resubmits=settings.jobs_max_resubmits)

        result = run_pass(REQUESTED)

        assert result.failed == 1
        assert _row(db_session, job.id).state == JobState.FAILED
        # The definition withdraws the subject so the source stops offering it.
        assert PROBE.failures == [(job.subject_key, "interrupted_repeatedly")]

    def test_finishes_a_job_whose_terminal_write_was_lost(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = _stale(make_job)
        run_pass(REQUESTED)
        engine.executions[
            execution_id(job.id, 2, job.execution_epoch)
        ].status = EngineStatus.SUCCEEDED

        result = run_pass(REQUESTED)

        assert result.completed == 1
        assert _row(db_session, job.id).state == JobState.COMPLETED

    def test_fails_a_job_the_engine_failed(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        engine.executions[
            execution_id(job.id, 1, job.execution_epoch)
        ].status = EngineStatus.FAILED

        result = run_pass(REQUESTED)

        assert result.failed == 1
        assert _row(db_session, job.id).state == JobState.FAILED
        assert PROBE.failures == [(job.subject_key, "engine_failed")]

    def test_reruns_work_stranded_on_a_dead_executor(
        self, engine: InlineJobEngine, make_job, make_work_executor, db_session
    ) -> None:
        dead = make_work_executor("dead-executor", stale=True)
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        stranded = engine.executions[execution_id(job.id, 1, job.execution_epoch)]
        stranded.status = EngineStatus.RUNNING
        stranded.executor_id = dead.executor_id

        result = run_pass(REQUESTED)

        assert result.interrupted == 1
        assert stranded.status is EngineStatus.CANCELLED
        assert _row(db_session, job.id).attempts == 2

    def test_reruns_work_of_another_version(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        old = engine.executions[execution_id(job.id, 1, job.execution_epoch)]
        old.app_version = "0.0.1-previous"

        run_pass(REQUESTED)

        assert old.status is EngineStatus.CANCELLED
        assert _row(db_session, job.id).attempts == 2

    def test_an_engine_that_cannot_cancel_leaves_the_job_for_the_next_pass(
        self, engine: InlineJobEngine, make_job, db_session: Session, monkeypatch
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        engine.executions[
            execution_id(job.id, 1, job.execution_epoch)
        ].app_version = "0.0.1-previous"

        def unreachable(_execution_id):
            raise RuntimeError("engine down")

        monkeypatch.setattr(engine, "cancel", unreachable)

        result = run_pass(REQUESTED)

        assert result.deferred == 1
        row = _row(db_session, job.id)
        assert (row.state, row.attempts) == (JobState.QUEUED, 1)

    def test_leaves_live_work_alone(
        self, engine: InlineJobEngine, make_job, db_session: Session
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)

        result = run_pass(REQUESTED)

        assert (result.submitted, result.interrupted) == (0, 0)
        assert result.outcomes.get("in_progress") == 1
        assert _row(db_session, job.id).attempts == 1


class TestDiscover:
    def test_submits_one_job_per_pending_subject(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        PROBE.items = _items("a", "b")

        result = run_pass(SOURCED)

        assert result.submitted == 2
        assert sorted(job.subject_key for job in _jobs(db_session)) == ["a", "b"]

    def test_the_job_takes_what_its_item_asked_for(
        self, engine: InlineJobEngine, db_session: Session, make_user
    ) -> None:
        owner = make_user()
        PROBE.items = _items(
            "mine", priority=WorkPriority.INTERACTIVE, owner_user_id=owner.id
        )

        run_pass(SOURCED)

        (job,) = _jobs(db_session)
        assert (job.priority, job.owner_user_id) == (WorkPriority.INTERACTIVE, owner.id)

    def test_a_subject_with_an_active_job_gets_no_second_one(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        make_job(kind=SOURCED, subject="busy", state=JobState.RUNNING, attempts=1)
        PROBE.items = _items("busy")

        result = run_pass(SOURCED)

        assert result.outcomes.get("already_active") == 1
        assert len(_jobs(db_session)) == 1

    def test_never_offers_more_than_the_lanes_headroom(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        # One slot, headroom factor 2: a backfill never floods the engine.
        headroom = engine.catalog.lanes[PROBE_LANE].headroom
        PROBE.items = _items(*[f"s{n}" for n in range(headroom + 3)])

        run_pass(SOURCED)

        assert len(_jobs(db_session)) == headroom

    def test_a_full_lane_offers_nothing(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        PROBE.items = _items(*[f"s{n}" for n in range(10)])
        run_pass(SOURCED)
        PROBE.calls = 0

        result = run_pass(SOURCED)

        assert result.outcomes.get("lane_full") == 1
        assert PROBE.calls == 0

    def test_a_full_batch_with_room_left_continues_in_the_same_pass(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        _overlay["jobs_reconcile_batch"] = 2
        engine.catalog.lanes[PROBE_LANE] = Lane(PROBE_LANE, 10)
        subjects = [f"s{n}" for n in range(5)]
        remaining = list(subjects)

        def drain_as_created():
            created = {job.subject_key for job in _jobs(db_session)}
            PROBE.items = _items(*[s for s in remaining if s not in created])

        PROBE.on_pending = drain_as_created

        result = run_pass(SOURCED)

        assert result.submitted == 5
        assert sorted(job.subject_key for job in _jobs(db_session)) == subjects

    def test_a_batch_of_subjects_already_in_flight_does_not_spin(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        # Regression: a source reporting subjects whose Jobs are still queued
        # filled the batch every loop, so the pass looped to its limit and then
        # nudged itself again, forever.
        _overlay["jobs_reconcile_batch"] = 2
        engine.catalog.lanes[PROBE_LANE] = Lane(PROBE_LANE, 10)
        for subject in ("a", "b"):
            make_job(kind=SOURCED, subject=subject)
        PROBE.items = _items("a", "b")

        result = run_pass(SOURCED)

        assert PROBE.calls == 1
        assert result.outcomes.get("already_active") == 2

    def test_records_a_declined_occurrence_as_a_cancelled_job(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        # A skip is a verdict, not an absence: the admin page shows it.
        occurrence = utcnow().replace(microsecond=0)
        PROBE.items = _items(
            "sched@1",
            skip=SkipReason.PREVIOUS_STILL_RUNNING,
            occurrence_at=occurrence,
        )

        result = run_pass(SOURCED)

        (job,) = _jobs(db_session)
        assert result.skipped == 1
        assert job.state == JobState.CANCELLED
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None and cursor.last_occurrence_at is not None

    def test_holds_back_a_subject_that_keeps_coming_straight_back(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        # Finished work the source still reports would otherwise spin: the
        # burst allowance runs it again a few times, then waits it out.
        for _ in range(settings.jobs_resubmit_burst):
            make_job(kind=SOURCED, subject="loop", state=JobState.COMPLETED)
        PROBE.items = _items("loop")

        result = run_pass(SOURCED)

        assert result.outcomes.get("cooling_down") == 1
        assert result.cooling_until is not None
        assert len(_jobs(db_session)) == settings.jobs_resubmit_burst
        assert any(ex.status is EngineStatus.DELAYED for ex in _passes(engine, SOURCED))

    def test_a_drain_is_never_held_back_for_finishing_often(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        # A drain's one subject comes back whenever new work arrives (every
        # upload projects the library again); holding it back stalled search.
        from dataclasses import replace

        drained = replace(engine.catalog.definitions[SOURCED], drain=True)
        engine.catalog.definitions[SOURCED] = drained
        for _ in range(settings.jobs_resubmit_burst):
            make_job(kind=SOURCED, subject="drain", state=JobState.COMPLETED)
        PROBE.items = _items("drain")

        result = run_pass(SOURCED)

        assert result.submitted == 1
        assert "cooling_down" not in result.outcomes

    def test_a_subject_finished_fewer_times_than_the_burst_runs_again(
        self, engine: InlineJobEngine, db_session: Session, make_job
    ) -> None:
        make_job(kind=SOURCED, subject="again", state=JobState.COMPLETED)
        PROBE.items = _items("again")

        result = run_pass(SOURCED)

        assert result.submitted == 1


class TestPass:
    def test_a_pass_already_claimed_elsewhere_does_nothing(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source=SOURCED,
                holder="another-executor",
                holder_expires_at=utcnow() + timedelta(minutes=1),
            )
        )
        db_session.commit()
        PROBE.items = _items("a")

        result = run_pass(SOURCED)

        assert result.outcomes == {"claimed_elsewhere": 1}
        assert _jobs(db_session) == []

    def test_an_expired_claim_is_taken_over(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        db_session.add(
            ReconcileCursor(
                source=SOURCED,
                holder="dead-executor",
                holder_expires_at=utcnow() - timedelta(seconds=1),
            )
        )
        db_session.commit()
        PROBE.items = _items("a")

        assert run_pass(SOURCED).submitted == 1

    def test_releases_its_claim(self, engine: InlineJobEngine, db_session) -> None:
        run_pass(SOURCED)

        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None
        assert (cursor.holder, cursor.last_pass_finished_at is not None) == (None, True)

    def test_a_nudge_during_a_pass_runs_it_again(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        # The dirty mark: work recorded while the pass was already past its
        # discovery must not wait for the next tick.
        def nudge_once():
            if PROBE.calls == 1:
                PROBE.items = _items("late")
                nudge(SOURCED)

        PROBE.on_pending = nudge_once

        run_pass(SOURCED)

        assert PROBE.calls == 2
        assert [job.subject_key for job in _jobs(db_session)] == ["late"]

    def test_a_pass_that_never_settles_hands_over(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        PROBE.on_pending = lambda: nudge(SOURCED)

        run_pass(SOURCED)

        assert PROBE.calls == 20
        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None and cursor.holder is None

    def test_asks_for_a_pass_at_the_sources_next_due_time(
        self, engine: InlineJobEngine
    ) -> None:
        PROBE.due = utcnow() + timedelta(seconds=90)

        run_pass(SOURCED)

        (delayed,) = [
            ex for ex in _passes(engine, SOURCED) if ex.status is EngineStatus.DELAYED
        ]
        assert 80 < (delayed.submission.delay_seconds or 0) <= 90

    def test_a_due_time_beyond_the_tick_is_left_to_the_tick(
        self, engine: InlineJobEngine
    ) -> None:
        PROBE.due = utcnow() + timedelta(
            seconds=settings.jobs_reconcile_interval_seconds + 60
        )

        run_pass(SOURCED)

        assert not [
            ex for ex in _passes(engine, SOURCED) if ex.status is EngineStatus.DELAYED
        ]

    def test_a_failing_source_releases_the_claim(
        self, engine: InlineJobEngine, db_session: Session
    ) -> None:
        def broken():
            raise RuntimeError("source query failed")

        PROBE.on_pending = broken

        with pytest.raises(RuntimeError):
            run_pass(SOURCED)

        db_session.expire_all()
        cursor = db_session.get(ReconcileCursor, SOURCED)
        assert cursor is not None and cursor.holder is None


def _stranded_pass(engine: InlineJobEngine, source: str, executor: str) -> str:
    """A reconcile pass a process was running when it died."""
    nudge(source)
    (stranded,) = _passes(engine, source)
    stranded.status = EngineStatus.RUNNING
    stranded.executor_id = executor
    return stranded.submission.execution_id


class TestSweepLostPasses:
    """A pass a dead process was running holds a reconcile slot until cancelled.

    Reconcile passes are not Jobs, so no repair ever interrupts them, and the
    lane is global: a process killed while its startup passes ran would keep
    every later pass, on every process, from starting.
    """

    def test_cancels_a_pass_stranded_on_a_dead_executor(
        self, engine: InlineJobEngine, make_work_executor
    ) -> None:
        dead = make_work_executor("dead-executor", stale=True)
        stranded = _stranded_pass(engine, SOURCED, dead.executor_id)

        assert sweep_lost_passes() == 1
        assert engine.executions[stranded].status is EngineStatus.CANCELLED

    def test_leaves_a_live_executors_pass_running(
        self, engine: InlineJobEngine, make_work_executor
    ) -> None:
        live = make_work_executor("live-executor")
        running = _stranded_pass(engine, SOURCED, live.executor_id)

        assert sweep_lost_passes() == 0
        assert engine.executions[running].status is EngineStatus.RUNNING

    def test_leaves_a_dead_executors_jobs_to_repair(
        self, engine: InlineJobEngine, make_job, make_work_executor
    ) -> None:
        # A Job's execution is repair's to interrupt, with its Job's record.
        dead = make_work_executor("dead-executor", stale=True)
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        execution = engine.executions[execution_id(job.id, 1, job.execution_epoch)]
        execution.status = EngineStatus.RUNNING
        execution.executor_id = dead.executor_id

        assert sweep_lost_passes() == 0
        assert execution.status is EngineStatus.RUNNING


class TestSweepForeignVersions:
    def test_cancels_every_execution_of_another_version(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        job = make_job(kind=REQUESTED)
        run_pass(REQUESTED)
        engine.executions[
            execution_id(job.id, 1, job.execution_epoch)
        ].app_version = "0.0.1-previous"

        assert sweep_foreign_versions() == 1
        assert (
            engine.executions[execution_id(job.id, 1, job.execution_epoch)].status
            is EngineStatus.CANCELLED
        )

    def test_leaves_this_versions_work_running(
        self, engine: InlineJobEngine, make_job
    ) -> None:
        make_job(kind=REQUESTED)
        run_pass(REQUESTED)

        assert sweep_foreign_versions() == 0


class TestInterrupt:
    def test_stale_engine_evidence_cannot_interrupt_a_new_attempt(
        self, db_session, make_job
    ):
        from app.modules.work.reconciler import Reason, _interrupt

        job = make_job(
            kind=JobKind.DERIVATIVES_MESH, state=JobState.RUNNING, attempts=2
        )
        job_id = job.id
        db_session.expunge(job)
        job.attempts = 1

        changed = _interrupt(job, Reason.INTERRUPTED_REPEATEDLY, now=utcnow())

        assert changed is False
        current = db_session.get(Job, job_id)
        assert (current.state, current.attempts, current.resubmits) == (
            JobState.RUNNING,
            2,
            0,
        )


class _PrioritizedSource(_Source):
    def __init__(self):
        self.budgets = []

    def pending_prioritized(self, session, *, now, budget):
        self.budgets.append(budget)
        chosen = []
        for priority, room in [
            (WorkPriority.INTERACTIVE, budget.interactive),
            (WorkPriority.BACKFILL, budget.backfill),
        ]:
            chosen.extend(
                [item for item in PROBE.items if item.priority is priority][:room]
            )
        return chosen[: budget.total]


class TestPriorityDiscovery:
    @staticmethod
    def source(engine):
        from dataclasses import replace

        source = _PrioritizedSource()
        definition = engine.catalog.definitions[SOURCED]
        engine.catalog.definitions[SOURCED] = replace(definition, source=source)
        return source

    def test_backfill_headroom_cannot_hide_interactive_intent(
        self, engine, db_session, make_job
    ):
        source = self.source(engine)
        for index in range(engine.catalog.lanes[PROBE_LANE].headroom):
            make_job(
                kind=REQUESTED, subject=f"old/{index}", priority=WorkPriority.BACKFILL
            )
        PROBE.items = _items("new", priority=WorkPriority.INTERACTIVE)
        run_pass(SOURCED)
        assert source.budgets
        assert source.budgets[0].backfill == 0
        assert source.budgets[0].interactive == 1
        (created,) = _jobs(db_session)
        assert created.subject_key == "new"
        assert created.priority is WorkPriority.INTERACTIVE

    def test_interactive_queue_preserves_a_backfill_discovery_slot(
        self, engine, db_session, make_job
    ):
        source = self.source(engine)
        make_job(
            kind=REQUESTED,
            subject="interactive/queued",
            priority=WorkPriority.INTERACTIVE,
        )
        PROBE.items = _items("backfill/new", priority=WorkPriority.BACKFILL)
        run_pass(SOURCED)
        assert source.budgets
        assert source.budgets[0].interactive == 0
        assert source.budgets[0].backfill == 1
        (created,) = _jobs(db_session)
        assert created.priority is WorkPriority.BACKFILL

    @pytest.mark.parametrize("state", [JobState.QUEUED, JobState.INTERRUPTED])
    def test_shared_lane_priority_quotas_bound_durable_pending_jobs(
        self, engine, db_session, make_job, state
    ):
        source = self.source(engine)
        for priority in (WorkPriority.INTERACTIVE, WorkPriority.BACKFILL):
            make_job(
                kind=REQUESTED, subject=priority.value, priority=priority, state=state
            )
        PROBE.items = _items("new/i", priority=WorkPriority.INTERACTIVE) + _items(
            "new/b", priority=WorkPriority.BACKFILL
        )
        run_pass(SOURCED)
        assert _jobs(db_session) == []
        assert source.budgets == []

    def test_running_jobs_do_not_consume_pending_discovery_slots(
        self, engine, db_session, make_job
    ):
        source = self.source(engine)
        make_job(
            kind=REQUESTED,
            subject="running/i",
            priority=WorkPriority.INTERACTIVE,
            state=JobState.RUNNING,
            attempts=1,
        )
        PROBE.items = _items("new/i", priority=WorkPriority.INTERACTIVE) + _items(
            "new/b", priority=WorkPriority.BACKFILL
        )
        run_pass(SOURCED)
        assert source.budgets
        assert source.budgets[0].interactive == 1
        assert source.budgets[0].backfill == 1
        assert source.budgets[0].total == 2
        assert {row.priority for row in _jobs(db_session)} == {
            WorkPriority.INTERACTIVE,
            WorkPriority.BACKFILL,
        }

    def test_running_backfill_holds_its_single_admission_slot(
        self, engine, db_session, make_job
    ):
        source = self.source(engine)
        make_job(
            kind=REQUESTED,
            subject="running/b",
            priority=WorkPriority.BACKFILL,
            state=JobState.RUNNING,
            attempts=1,
        )
        PROBE.items = _items("new/i", priority=WorkPriority.INTERACTIVE) + _items(
            "new/b", priority=WorkPriority.BACKFILL
        )
        run_pass(SOURCED)
        assert source.budgets
        assert source.budgets[0].interactive == 1
        assert source.budgets[0].backfill == 0
        assert source.budgets[0].total == 1
        (created,) = _jobs(db_session)
        assert created.subject_key == "new/i"
        assert created.priority is WorkPriority.INTERACTIVE


@pytest.fixture
def priority_admission_vault(tmp_path):
    from sqlalchemy import event
    from sqlmodel import SQLModel, create_engine

    from app.db.session import (
        SQLiteSessionFactory,
        _set_sqlite_pragmas,
        get_session_factory,
        override_session_factory,
    )

    database = create_engine(
        f"sqlite:///{tmp_path / 'priority-admission.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(database, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(database)
    previous = get_session_factory()
    factory = SQLiteSessionFactory(database)
    override_session_factory(factory)
    try:
        yield factory
    finally:
        override_session_factory(previous)
        database.dispose()


class TestPriorityAdmission:
    def test_concurrent_sources_share_one_interactive_queue_slot(
        self, priority_admission_vault, engine, db_session, monkeypatch
    ):
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context
        from dataclasses import replace
        from threading import Barrier

        from app.modules.work.reconciler import PassResult, _discover

        barrier = Barrier(2)

        class ConcurrentSource(_Source):
            def __init__(self, subject):
                self.subject = subject

            def pending_prioritized(self, session, *, now, budget):
                barrier.wait(timeout=5)
                return [WorkItem(self.subject, priority=WorkPriority.INTERACTIVE)]

        definitions = []
        for kind in (SOURCED, REQUESTED):
            definition = replace(
                engine.catalog.definitions[kind],
                source=ConcurrentSource(f"concurrent/{kind.value}"),
            )
            engine.catalog.definitions[kind] = definition
            definitions.append(definition)
        monkeypatch.setattr("app.modules.work.reconciler.submit", lambda _job: None)

        def discover(definition):
            from app.db.session import override_session_factory

            override_session_factory(priority_admission_vault)
            _discover(definition, now=utcnow(), result=PassResult())

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(copy_context().run, discover, definition)
                for definition in definitions
            ]
            for future in futures:
                future.result(timeout=10)
        db_session.expire_all()
        queued = db_session.exec(
            select(Job).where(
                Job.state == JobState.QUEUED,
                Job.priority == WorkPriority.INTERACTIVE,
            )
        ).all()
        assert len(queued) == 1
        assert queued[0].kind in (SOURCED, REQUESTED)

    def test_admission_rechecks_pending_counts_after_source_discovery(
        self, engine, db_session, make_job, monkeypatch
    ):
        from dataclasses import replace

        from app.modules.work.reconciler import PassResult, _discover

        class ArrivingSource(_Source):
            def pending_prioritized(self, session, *, now, budget):
                make_job(
                    kind=REQUESTED,
                    subject="arrived/i",
                    priority=WorkPriority.INTERACTIVE,
                )
                return [WorkItem("late/i", priority=WorkPriority.INTERACTIVE)]

        definition = replace(
            engine.catalog.definitions[SOURCED], source=ArrivingSource()
        )
        monkeypatch.setattr("app.modules.work.reconciler.submit", lambda _job: None)
        _discover(definition, now=utcnow(), result=PassResult())
        assert _jobs(db_session) == []
        assert len(_jobs(db_session, REQUESTED)) == 1

    @pytest.mark.parametrize("authority", ["expired", "replaced"])
    def test_lost_holder_cannot_admit_work(
        self, engine, db_session, monkeypatch, authority
    ):
        from app.db.models import WorkFence
        from app.modules.work.reconciler import (
            PassNote,
            PassResult,
            _create_and_submit,
            _DiscoveryLease,
        )

        now = utcnow()
        name = f"discovery:{PROBE_LANE.value}"
        db_session.add(
            WorkFence(
                name=name,
                holder="old" if authority == "expired" else "replacement",
                reason="qualification",
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now
                + timedelta(seconds=-1 if authority == "expired" else 60),
            )
        )
        db_session.commit()
        submissions = []
        monkeypatch.setattr("app.modules.work.reconciler.submit", submissions.append)
        result = PassResult()
        created = _create_and_submit(
            engine.catalog.definitions[SOURCED],
            WorkItem("lost/i", priority=WorkPriority.INTERACTIVE),
            now=now,
            result=result,
            lease=_DiscoveryLease(name, "old"),
        )
        assert created == 0
        assert _jobs(db_session) == []
        assert submissions == []
        assert result.outcomes[PassNote.CLAIMED_ELSEWHERE] == 1
        assert result.full is False
        db_session.expire_all()
        retained = db_session.get(WorkFence, name)
        assert retained is not None
        assert retained.holder == ("old" if authority == "expired" else "replacement")

    def test_previous_pass_cannot_release_a_replacement_holder(
        self, engine, db_session, monkeypatch
    ):
        from app.db.models import WorkFence
        from app.modules.work import reconciler

        TestPriorityDiscovery.source(engine)
        PROBE.items = _items("retained/i", priority=WorkPriority.INTERACTIVE)
        original = reconciler._create_and_submit

        def replace_holder(definition, item, *, now, result, lease):
            assert lease is not None
            row = db_session.get(WorkFence, lease.name)
            assert row is not None and row.holder == lease.holder
            row.holder = "replacement"
            row.expires_at = utcnow() + timedelta(seconds=60)
            db_session.add(row)
            db_session.commit()
            return original(definition, item, now=now, result=result, lease=lease)

        monkeypatch.setattr(reconciler, "_create_and_submit", replace_holder)
        result = reconciler.PassResult()
        reconciler._discover(
            engine.catalog.definitions[SOURCED], now=utcnow(), result=result
        )
        assert _jobs(db_session) == []
        assert result.full is False
        db_session.expire_all()
        retained = db_session.get(WorkFence, f"discovery:{PROBE_LANE.value}")
        assert retained is not None and retained.holder == "replacement"
        assert [item.subject_key for item in PROBE.items] == ["retained/i"]

    def test_create_error_releases_the_discovery_fence(
        self, engine, db_session, monkeypatch
    ):
        from app.db.models import WorkFence
        from app.modules.work import reconciler

        TestPriorityDiscovery.source(engine)
        PROBE.items = _items("retained/i", priority=WorkPriority.INTERACTIVE)

        def fail_create(**_kwargs):
            raise ValueError("qualification create failure")

        monkeypatch.setattr(reconciler.jobs, "create", fail_create)
        result = reconciler.PassResult()
        with pytest.raises(ValueError, match="qualification create failure"):
            reconciler._discover(
                engine.catalog.definitions[SOURCED], now=utcnow(), result=result
            )
        assert _jobs(db_session) == []
        assert db_session.get(WorkFence, f"discovery:{PROBE_LANE.value}") is None
        assert result.full is False
        assert [item.subject_key for item in PROBE.items] == ["retained/i"]


class TestFIFORecovery:
    @staticmethod
    def configure(engine):
        from app.modules.work.contracts import LaneOrder

        engine.catalog.lanes[PROBE_LANE] = Lane(
            PROBE_LANE, 2, queue_order=LaneOrder.FIFO
        )

    def test_legacy_backfill_queue_recovers_one_oldest_attempt(
        self, engine, db_session, make_job
    ):
        self.configure(engine)
        now = utcnow()
        backlog = [
            make_job(
                kind=REQUESTED,
                subject=f"legacy/{index}",
                priority=WorkPriority.BACKFILL,
                created_at=now - timedelta(minutes=3 - index),
                updated_at=now - timedelta(minutes=3 - index),
            )
            for index in range(3)
        ]
        identities = [(row.id, row.execution_epoch) for row in backlog]
        result = run_pass(REQUESTED)
        assert result.submitted == 1
        assert result.deferred == 2
        assert [_row(db_session, identity).attempts for identity, _ in identities] == [
            1,
            0,
            0,
        ]
        assert execution_id(identities[0][0], 1, identities[0][1]) in engine.executions
        assert all(
            execution_id(identity, 1, epoch) not in engine.executions
            for identity, epoch in identities[1:]
        )
        assert all(
            _row(db_session, identity).state is JobState.QUEUED
            for identity, _ in identities
        )

    def test_running_backfill_blocks_another_definition_recovery(
        self, engine, db_session, make_job
    ):
        self.configure(engine)
        running = make_job(
            kind=REQUESTED, subject="running/b", priority=WorkPriority.BACKFILL
        )
        running_id, running_epoch = running.id, running.execution_epoch
        run_pass(REQUESTED)
        engine.executions[
            execution_id(running_id, 1, running_epoch)
        ].status = EngineStatus.RUNNING
        row = _row(db_session, running_id)
        row.state = JobState.RUNNING
        db_session.add(row)
        db_session.commit()
        older = make_job(
            kind=SOURCED,
            subject="old/b",
            priority=WorkPriority.BACKFILL,
            created_at=utcnow() - timedelta(days=1),
        )
        interactive = make_job(
            kind=SOURCED, subject="new/i", priority=WorkPriority.INTERACTIVE
        )
        older_id, interactive_id = older.id, interactive.id
        result = run_pass(SOURCED)
        assert result.submitted == 1
        assert result.deferred == 1
        assert _row(db_session, older_id).attempts == 0
        assert _row(db_session, older_id).state is JobState.QUEUED
        assert _row(db_session, interactive_id).attempts == 1
        assert _row(db_session, running_id).attempts == 1
        assert (
            engine.executions[execution_id(running_id, 1, running_epoch)].status
            is EngineStatus.RUNNING
        )

    def test_settled_backfill_releases_the_next_oldest_attempt(
        self, engine, db_session, make_job
    ):
        self.configure(engine)
        first = make_job(
            kind=REQUESTED, subject="first/b", priority=WorkPriority.BACKFILL
        )
        first_id, first_epoch = first.id, first.execution_epoch
        run_pass(REQUESTED)
        second = make_job(
            kind=REQUESTED, subject="second/b", priority=WorkPriority.BACKFILL
        )
        second_id, second_epoch = second.id, second.execution_epoch
        engine.executions[
            execution_id(first_id, 1, first_epoch)
        ].status = EngineStatus.SUCCEEDED
        result = run_pass(REQUESTED)
        assert result.completed == 1
        assert result.submitted == 1
        assert _row(db_session, first_id).state is JobState.COMPLETED
        assert _row(db_session, second_id).attempts == 1
        assert execution_id(second_id, 1, second_epoch) in engine.executions

    @pytest.mark.parametrize("backlog_size", [10, 100])
    def test_backfill_recovery_selects_one_owner_per_pending_batch(
        self, engine, db_session, make_job, backlog_size
    ):
        from sqlalchemy import event

        self.configure(engine)
        now = utcnow()
        backlog = [
            make_job(
                kind=REQUESTED,
                subject=f"legacy/batch/{index}",
                priority=WorkPriority.BACKFILL,
                created_at=now + timedelta(microseconds=index),
                updated_at=now + timedelta(microseconds=index),
            )
            for index in range(backlog_size)
        ]
        identities = [row.id for row in backlog]
        owner_selections = []

        def capture_owner(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            sql = " ".join(statement.lower().split())
            if (
                sql.startswith("select ")
                and "order by case" in sql
                and "jobs.created_at" in sql
            ):
                owner_selections.append(sql)

        database = db_session.get_bind()
        event.listen(database, "before_cursor_execute", capture_owner)
        try:
            result = run_pass(REQUESTED)
        finally:
            event.remove(database, "before_cursor_execute", capture_owner)
        assert len(owner_selections) == 1
        assert result.submitted == 1
        assert result.deferred == backlog_size - 1
        assert [_row(db_session, identity).attempts for identity in identities] == [
            1,
            *([0] * (backlog_size - 1)),
        ]
        assert all(
            _row(db_session, identity).state is JobState.QUEUED
            for identity in identities
        )

    @staticmethod
    def complete_recovered_backfill(engine, db_session, identities):
        for _ in range(6):
            for identity, epoch in identities:
                execution = engine.executions.get(execution_id(identity, 1, epoch))
                if execution is not None:
                    execution.status = EngineStatus.SUCCEEDED
            run_pass(REQUESTED)
            if all(
                _row(db_session, identity).state is JobState.COMPLETED
                for identity, _ in identities
            ):
                break

    def test_oldest_recovery_owner_cannot_be_hidden_by_the_page(
        self, engine, db_session, make_job, monkeypatch
    ):
        self.configure(engine)
        monkeypatch.setitem(_overlay, "jobs_reconcile_batch", 2)
        now = utcnow()
        oldest = make_job(
            kind=REQUESTED,
            subject="oldest/outside-page",
            priority=WorkPriority.BACKFILL,
            created_at=now - timedelta(days=3),
            updated_at=now,
        )
        others = [
            make_job(
                kind=REQUESTED,
                subject=f"newer/page/{index}",
                priority=WorkPriority.BACKFILL,
                created_at=now - timedelta(days=2 - index),
                updated_at=now - timedelta(minutes=3 - index),
            )
            for index in range(2)
        ]
        identities = [(row.id, row.execution_epoch) for row in [oldest, *others]]
        result = run_pass(REQUESTED)
        assert result.submitted == 1
        assert _row(db_session, oldest.id).attempts == 1
        assert [_row(db_session, row.id).attempts for row in others] == [0, 0]
        self.complete_recovered_backfill(engine, db_session, identities)
        assert all(
            _row(db_session, identity).state is JobState.COMPLETED
            for identity, _ in identities
        )
        assert [_row(db_session, identity).attempts for identity, _ in identities] == [
            1,
            1,
            1,
        ]

    def test_exhausted_cached_owner_releases_the_next_attempt(
        self, engine, db_session, make_job
    ):
        self.configure(engine)
        exhausted = _stale(
            make_job,
            priority=WorkPriority.BACKFILL,
            resubmits=settings.jobs_max_resubmits,
            created_at=utcnow() - timedelta(days=1),
        )
        exhausted_id = exhausted.id
        next_job = make_job(
            kind=REQUESTED, subject="after-exhausted/b", priority=WorkPriority.BACKFILL
        )
        next_id, next_epoch = next_job.id, next_job.execution_epoch
        result = run_pass(REQUESTED)
        assert result.interrupted == 1
        assert result.failed == 1
        assert result.submitted == 1
        failed = _row(db_session, exhausted_id)
        assert failed.state is JobState.FAILED
        assert failed.attempts == 1
        assert failed.resubmits == settings.jobs_max_resubmits + 1
        assert _row(db_session, next_id).attempts == 1
        assert _row(db_session, next_id).state is JobState.QUEUED
        assert execution_id(next_id, 1, next_epoch) in engine.executions
        assert PROBE.failures == [(failed.subject_key, "interrupted_repeatedly")]

    @pytest.mark.parametrize("terminal", [JobState.FAILED, JobState.CANCELLED])
    def test_public_retry_preserves_accepted_queued_backfill_authority(
        self, engine, db_session, make_job, make_user, terminal
    ):
        from app.modules.work import service
        from app.modules.work.contracts import LaneOrder

        engine.catalog.lanes[PROBE_LANE] = Lane(
            PROBE_LANE, 1, queue_order=LaneOrder.FIFO
        )
        actor = make_user()
        old = make_job(
            kind=REQUESTED,
            subject="older/retried/b",
            owner=actor,
            priority=WorkPriority.BACKFILL,
            state=terminal,
            attempts=1,
            created_at=utcnow() - timedelta(days=2),
        )
        old_id, original_epoch = old.id, old.execution_epoch
        running = make_job(
            kind=REQUESTED, subject="running/i", priority=WorkPriority.INTERACTIVE
        )
        running_id, running_epoch = running.id, running.execution_epoch
        run_pass(REQUESTED)
        engine.executions[
            execution_id(running_id, 1, running_epoch)
        ].status = EngineStatus.RUNNING
        running_row = _row(db_session, running_id)
        running_row.state = JobState.RUNNING
        db_session.add(running_row)
        db_session.commit()
        accepted = make_job(
            kind=REQUESTED, subject="accepted/queued/b", priority=WorkPriority.BACKFILL
        )
        accepted_id, accepted_epoch = accepted.id, accepted.execution_epoch
        run_pass(REQUESTED)
        accepted_execution = execution_id(accepted_id, 1, accepted_epoch)
        accepted_evidence = engine.evidence([accepted_execution])
        assert accepted_evidence[accepted_execution].status is EngineStatus.QUEUED
        assert _row(db_session, accepted_id).submitted_epoch == accepted_epoch
        later = make_job(
            kind=REQUESTED, subject="later/i", priority=WorkPriority.INTERACTIVE
        )
        later_id, later_epoch = later.id, later.execution_epoch
        retry_status = service.retry(old_id, actor=actor)
        retried_epoch = _row(db_session, old_id).execution_epoch
        assert retry_status.state is JobState.QUEUED
        assert retried_epoch != original_epoch

        result = run_pass(REQUESTED)

        assert result.submitted == 1
        assert _row(db_session, old_id).attempts == 1
        assert _row(db_session, old_id).submitted_epoch == original_epoch
        assert execution_id(old_id, 2, retried_epoch) not in engine.executions
        assert _row(db_session, accepted_id).attempts == 1
        assert (
            engine.evidence([accepted_execution])[accepted_execution].status
            is EngineStatus.QUEUED
        )
        assert _row(db_session, later_id).attempts == 1
        assert execution_id(later_id, 1, later_epoch) in engine.executions
        assert _row(db_session, running_id).state is JobState.RUNNING

    @staticmethod
    def crash_after_backfill_acceptance(
        original_submit, backfill_id, retry_id, actor, observed, submission
    ):
        from app.modules.work import service
        from app.modules.work.contracts import JobSubmission

        outcome = original_submit(submission)
        if isinstance(submission, JobSubmission) and submission.job_id == backfill_id:
            service.retry(retry_id, actor=actor)
            observed.append(run_pass(REQUESTED))
            raise RuntimeError("worker crashed after actual engine acceptance")
        return outcome

    def test_acceptance_before_recording_keeps_backfill_authority(
        self, engine, db_session, make_job, make_user, monkeypatch
    ):
        from functools import partial

        self.configure(engine)
        actor = make_user()
        old = make_job(
            kind=REQUESTED,
            subject="old/retried/b",
            owner=actor,
            priority=WorkPriority.BACKFILL,
            state=JobState.FAILED,
            attempts=1,
            created_at=utcnow() - timedelta(days=2),
        )
        old_id = old.id
        current = make_job(
            kind=SOURCED,
            subject="accepted/before-recording",
            priority=WorkPriority.BACKFILL,
        )
        current_id, current_epoch = current.id, current.execution_epoch
        interactive = make_job(
            kind=REQUESTED,
            subject="interactive/during-acceptance",
            priority=WorkPriority.INTERACTIVE,
        )
        interactive_id = interactive.id
        observed = []
        original_submit = engine.submit
        monkeypatch.setattr(
            engine,
            "submit",
            partial(
                self.crash_after_backfill_acceptance,
                original_submit,
                current_id,
                old_id,
                actor,
                observed,
            ),
        )

        crashed = run_pass(SOURCED)

        assert crashed.submitted == 0
        assert crashed.deferred == 1
        (during_acceptance,) = observed
        assert during_acceptance.submitted == 1
        assert _row(db_session, interactive_id).attempts == 1
        assert _row(db_session, old_id).attempts == 1
        retried_epoch = _row(db_session, old_id).execution_epoch
        assert execution_id(old_id, 2, retried_epoch) not in engine.executions
        unrecorded = _row(db_session, current_id)
        assert unrecorded.attempts == 0
        assert unrecorded.submitted_epoch is None
        assert unrecorded.backfill_admission_epoch == current_epoch
        accepted_id = execution_id(current_id, 1, current_epoch)
        accepted_execution = engine.executions[accepted_id]
        assert accepted_execution.status is EngineStatus.QUEUED
        monkeypatch.setattr(engine, "submit", original_submit)

        run_pass(REQUESTED)
        recovered = run_pass(SOURCED)

        assert _row(db_session, old_id).attempts == 1
        assert recovered.submitted == 1
        recorded = _row(db_session, current_id)
        assert recorded.attempts == 1
        assert recorded.submitted_epoch == current_epoch
        assert recorded.backfill_admission_epoch == current_epoch
        assert engine.executions[accepted_id] is accepted_execution

    def test_busy_lane_fence_defers_backfill_without_blocking_interactive(
        self, engine, db_session, make_job
    ):
        from app.db.models import WorkFence
        from app.modules.work import fences

        self.configure(engine)
        backfill = make_job(
            kind=REQUESTED, subject="busy/b", priority=WorkPriority.BACKFILL
        )
        interactive = make_job(
            kind=REQUESTED, subject="busy/i", priority=WorkPriority.INTERACTIVE
        )
        backfill_id = backfill.id
        interactive_id, interactive_epoch = interactive.id, interactive.execution_epoch
        name = f"discovery:{PROBE_LANE.value}"
        fences.acquire(name, holder="other-pass", reason="qualify busy repair")

        result = run_pass(REQUESTED)

        assert result.submitted == 1
        assert result.deferred == 1
        assert result.full is False
        pending = _row(db_session, backfill_id)
        assert pending.state is JobState.QUEUED
        assert pending.attempts == 0
        assert pending.backfill_admission_epoch is None
        assert _row(db_session, interactive_id).attempts == 1
        assert execution_id(interactive_id, 1, interactive_epoch) in engine.executions
        held = db_session.get(WorkFence, name)
        assert held is not None and held.holder == "other-pass"

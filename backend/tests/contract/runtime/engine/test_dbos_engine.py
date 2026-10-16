"""Actual engine queue ordering, polling, persistent ticks and DBOS system-state upgrades.

The engine finds queued work only by polling. A lane that was just handed
interactive work is polled faster for a short window and then returns to its
own interval. When this goes red, either an upload waits half an ordinary
polling interval for nothing, or an idle server keeps paying for the fast one.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from dbos import DBOS
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import select

from app.core.config import _overlay, settings
from app.db.models import Job, JobState, LaneName, WorkPriority
from app.db.session import get_session_factory
from app.modules.work import catalog as catalog_module
from app.modules.work import submission
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import (
    Deduplicated,
    EngineStatus,
    ExecutionKind,
    JobSubmission,
    SubmitOutcome,
)
from app.runtime.engine.dbos_engine import TICK_WORKFLOW, system_database_url
from tests.containers import fresh_postgres_database
from tests.contract.modules.work._harness import (
    FAST,
    PLAIN,
    RECORD,
    DbosHarness,
    InlineHarness,
    contract_catalog,
    gated,
    shared_app_db,
)
from tests.factories.ops import build_job

FIXTURES = Path(__file__).parents[3] / "fixtures" / "dbos-2.31.1"
VERSION = "contract-sdk-upgrade"


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def system_db(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'engine.sqlite'}", None
    return system_database_url(fresh_postgres_database("dbos_upgrade"))


@contextmanager
def running_engine(system_db):
    RECORD.steps.clear()
    RECORD.behaviour.clear()
    RECORD.discover.clear()
    RECORD.running = RECORD.peak = 0
    catalog = contract_catalog()
    with shared_app_db():
        harness = DbosHarness(
            catalog,
            system_db[0],
            schema=system_db[1],
            app_version=VERSION,
            listen_lanes=[],
        )
        catalog_module.bind(harness.engine, catalog)
        try:
            yield harness
        finally:
            harness.close()
            catalog_module.bind(None, None)


@pytest.fixture
def engine(system_db):
    with running_engine(system_db) as harness:
        yield harness


@pytest.fixture
def upgraded(system_db):
    url, schema = system_db
    dialect = "postgres" if schema else "sqlite"
    sql = (FIXTURES / f"{dialect}.sql").read_text()
    if schema:
        db = create_engine(url)
        with db.begin() as connection:
            connection.exec_driver_sql(sql)
            assert (
                connection.exec_driver_sql(
                    "SELECT version FROM dbos.dbos_migrations"
                ).scalar_one()
                == 108
            )
        db.dispose()
    else:
        with sqlite3.connect(url.removeprefix("sqlite:///")) as connection:
            connection.executescript(sql)
            assert connection.execute(
                "SELECT version FROM dbos_migrations"
            ).fetchone() == (108,)
    with running_engine(system_db) as harness:
        with get_session_factory().scoped_session() as session:
            build_job(
                session,
                id="dbos-231-job",
                kind=PLAIN,
                subject="upgrade/queued",
                attempts=1,
            )
        yield harness


class TestPersistentTick:
    def test_relaunch_keeps_one_schedule(self, engine):
        engine.relaunch(app_version=VERSION, listen_lanes=[])

        schedules = DBOS.list_schedules()
        assert [schedule["schedule_name"] for schedule in schedules] == [TICK_WORKFLOW]

    def test_relaunch_updates_the_cadence(self, engine):
        engine.tick_seconds = 300
        engine.relaunch(app_version=VERSION, listen_lanes=[])

        schedule = DBOS.get_schedule(TICK_WORKFLOW)
        assert schedule is not None
        assert schedule["schedule"] == "*/5 * * * *"

    def test_reset_recreates_the_schedule(self, engine):
        engine.engine.reset()
        engine.engine.launch(listen_lanes=[])

        assert [s["schedule_name"] for s in DBOS.list_schedules()] == [TICK_WORKFLOW]

    def test_tick_recovers_work_without_a_nudge(self, engine):
        engine.tick_seconds = 1
        engine.relaunch(app_version=VERSION, listen_lanes=None)
        RECORD.discover.add("tick/missed-nudge")

        engine.wait_for(lambda: "tick/missed-nudge" in RECORD.firsts())
        engine.settle()

        with get_session_factory().scoped_session() as session:
            found = session.exec(
                select(Job).where(Job.subject_key == "tick/missed-nudge")
            ).one()
            assert found.state == JobState.COMPLETED
        assert "tick/missed-nudge" not in RECORD.discover


def _submission(job, *, expedite: bool = True) -> JobSubmission:
    return JobSubmission(
        execution_id=submission.execution_id(job.id, 1, job.execution_epoch),
        job_id=job.id,
        definition=job.kind,
        subject_key=job.subject_key,
        lane=FAST,
        priority=WorkPriority.INTERACTIVE,
        attempt=1,
        execution_epoch=job.execution_epoch,
        routing=Deduplicated(submission.dedupe_key(job.kind, job.subject_key)),
        expedite=expedite,
    )


def _interval(harness, lane: LaneName) -> float:
    return harness.engine._queues[lane].polling_interval_sec


class TestLaneBoost:
    def test_a_job_a_request_waits_on_boosts_its_lane(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 0.02
        job_id = engine.job(PLAIN, "boost/1")
        with get_session_factory().scoped_session() as session:
            job = session.get(Job, job_id)

        engine.engine.submit(_submission(job))

        assert (engine.engine.boosted_lanes(), _interval(engine, FAST)) == (
            frozenset({FAST}),
            0.02,
        )

    def test_a_job_nobody_waits_on_leaves_its_lane_alone(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 0.02
        job_id = engine.job(PLAIN, "boost/2")
        with get_session_factory().scoped_session() as session:
            job = session.get(Job, job_id)

        engine.engine.submit(_submission(job, expedite=False))

        assert (engine.engine.boosted_lanes(), _interval(engine, FAST)) == (
            frozenset(),
            0.1,
        )

    def test_a_reconcile_pass_boosts_nothing(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 0.02

        submission.nudge(PLAIN)

        assert engine.engine.boosted_lanes() == frozenset()

    def test_a_boost_ends_after_its_window(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 0.02
        _overlay["jobs_queue_boost_seconds"] = 0.2
        job_id = engine.job(PLAIN, "boost/3")
        with get_session_factory().scoped_session() as session:
            job = session.get(Job, job_id)
        engine.engine.submit(_submission(job))

        engine.wait_for(lambda: not engine.engine.boosted_lanes())

        assert _interval(engine, FAST) == 0.1

    def test_a_window_of_zero_disables_the_boost(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 0.02
        _overlay["jobs_queue_boost_seconds"] = 0.0
        job_id = engine.job(PLAIN, "boost/4")
        with get_session_factory().scoped_session() as session:
            job = session.get(Job, job_id)

        engine.engine.submit(_submission(job))

        assert (engine.engine.boosted_lanes(), _interval(engine, FAST)) == (
            frozenset(),
            0.1,
        )

    def test_a_boost_no_faster_than_the_lane_changes_nothing(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 1.0
        job_id = engine.job(PLAIN, "boost/5")
        with get_session_factory().scoped_session() as session:
            job = session.get(Job, job_id)

        engine.engine.submit(_submission(job))

        assert engine.engine.boosted_lanes() == frozenset()

    def test_a_boosted_job_still_runs_to_completion(self, engine):
        _overlay["jobs_boosted_queue_poll_interval_seconds"] = 0.02
        engine.relaunch(app_version=VERSION, listen_lanes=None)
        job_id = engine.job(PLAIN, "boost/6")

        submission.submit(job_id, expedite=True)
        engine.settle()

        assert engine.state(job_id) == JobState.COMPLETED


@contextmanager
def _statements_on(url: str) -> Iterator[set[tuple[str, tuple]]]:
    """Every distinct workflow query the engine sends to the database at ``url``."""
    seen: set[tuple[str, tuple]] = set()

    def capture(conn, cursor, statement, parameters, context, executemany):
        if str(conn.engine.url) == url and "workflow_status" in statement:
            seen.add((statement, tuple(parameters)))

    event.listen(Engine, "before_cursor_execute", capture)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", capture)


class TestIdlePolling:
    """An idle server polls every lane forever (#476).

    On SQLite a poll that cannot use an index reads every retained workflow row,
    so idle CPU grows with history: ~30% on a mini PC holding a week of
    reconcile passes. The plan, not the row count, decides this.
    """

    def test_no_poll_scans_the_workflow_table_on_sqlite(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'engine.sqlite'}"
        with running_engine((url, None)) as harness:
            # Launch recovers this executor's work once; polling is what repeats.
            harness.relaunch(app_version=VERSION, listen_lanes=None)
            with _statements_on(url) as seen:
                lanes = {lane.value for lane in harness.catalog.lanes}
                harness.wait_for(
                    lambda: lanes <= {p for _, ps in list(seen) for p in ps}
                )
                polled = set(seen)

        with sqlite3.connect(url.removeprefix("sqlite:///")) as connection:
            scans = {
                statement: detail
                for statement, parameters in polled
                for *_, detail in connection.execute(
                    f"EXPLAIN QUERY PLAN {statement}", parameters
                )
                if detail.startswith("SCAN") and "workflow_status" in detail
            }
        assert scans == {}


def _epoch_of(job_id: str) -> str:
    with get_session_factory().scoped_session() as session:
        row = session.get(Job, job_id)
        assert row is not None
        return row.execution_epoch


def _waiting_job(subject: str) -> Job:
    with get_session_factory().scoped_session() as session:
        job = build_job(session, kind=PLAIN, subject=subject)
        session.expunge(job)
    return job


class TestSubmission:
    """A submission is one enqueue; nothing is read from the engine first.

    It used to read the workflow's status (joined with its input and output)
    before every enqueue, only to tell a reused id from a new one. The insert
    already reports that, so every Job and every reconcile pass paid a read for
    nothing.
    """

    def test_a_submission_reads_no_workflow_before_enqueuing(self, engine):
        job = _waiting_job("submission/one-write")
        caller = threading.get_ident()
        statements: list[str] = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if threading.get_ident() == caller and "workflow_status" in statement:
                statements.append(statement.lstrip().split(None, 1)[0].upper())

        event.listen(Engine, "before_cursor_execute", capture)
        try:
            outcome = engine.engine.submit(_submission(job))
        finally:
            event.remove(Engine, "before_cursor_execute", capture)

        assert (outcome, statements) == (SubmitOutcome.ACCEPTED, ["INSERT"])


class TestRecovery:
    """An execution DBOS recovers is bounded like one the reconciler resubmits.

    Only a pinned executor id recovers its own work after a restart, and a
    pinned executor never goes stale, so the resubmit budget never applies to
    it. A step that kills its process would otherwise restart it a hundred
    times, DBOS's default.
    """

    def test_gives_up_on_an_execution_recovered_past_the_resubmit_budget(
        self, tmp_path
    ):
        url = f"sqlite:///{tmp_path / 'engine.sqlite'}"
        gate = threading.Event()
        with running_engine((url, None)) as harness:
            harness.relaunch(app_version=VERSION, listen_lanes=None)
            job_id = harness.job(PLAIN, "recovery/poison", behaviour=gated(gate))
            submission.submit(job_id)
            harness.started("recovery/poison")
            execution = submission.execution_id(job_id, 1, _epoch_of(job_id))
            harness.engine.shutdown()
            # Stands in for that many crashes, each recovered on restart.
            with sqlite3.connect(url.removeprefix("sqlite:///")) as connection:
                connection.execute(
                    "UPDATE workflow_status SET recovery_attempts = ? "
                    "WHERE workflow_uuid = ?",
                    (settings.jobs_max_resubmits + 1, execution),
                )

            harness.relaunch(app_version=VERSION, listen_lanes=None)
            harness.wait_for(
                lambda: (
                    harness.engine.evidence([execution])[execution].status
                    is EngineStatus.FAILED
                )
            )
            gate.set()

            assert harness.engine.evidence([execution])[execution].status is (
                EngineStatus.FAILED
            )


class TestInFlightReads:
    """Lane depth and the active list read only what is in flight.

    Every discovery pass, the admin page (once per lane), housekeeping and the
    tick's sweeps read them. DBOS's own listing orders by creation time, so on
    SQLite it walked every retained workflow row: 137 ms per read at 200,000
    rows, which a bulk import and a week of reconcile passes reach.
    """

    def test_counts_waiting_executions_as_queued(self, engine):
        queued = _waiting_job("in-flight/queued")
        delayed = _waiting_job("in-flight/delayed")
        engine.engine.submit(_submission(queued))
        engine.engine.submit(replace(_submission(delayed), delay_seconds=600))

        depth = engine.engine.lane_depth(FAST)

        assert (depth.queued, depth.running) == (2, 0)

    def test_lists_waiting_executions_as_active(self, engine):
        queued = _waiting_job("in-flight/queued")
        delayed = _waiting_job("in-flight/delayed")
        engine.engine.submit(_submission(queued))
        engine.engine.submit(replace(_submission(delayed), delay_seconds=600))

        active = {
            execution.execution_id: (execution.kind, execution.status)
            for execution in engine.engine.active()
        }

        assert active == {
            _submission(queued).execution_id: (ExecutionKind.JOB, EngineStatus.QUEUED),
            _submission(delayed).execution_id: (
                ExecutionKind.JOB,
                EngineStatus.DELAYED,
            ),
        }

    def test_no_in_flight_read_walks_the_workflow_history_on_sqlite(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'engine.sqlite'}"
        with running_engine((url, None)) as harness:
            harness.engine.submit(_submission(_waiting_job("in-flight/plan")))
            with _statements_on(url) as seen:
                harness.engine.lane_depth(FAST)
                harness.engine.active()
                read = set(seen)

        with sqlite3.connect(url.removeprefix("sqlite:///")) as connection:
            # A partial index holds only in-flight (or delayed) rows, so a scan
            # of one is bounded by the work in flight, not by history.
            partial = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' "
                    "AND tbl_name = 'workflow_status' AND sql LIKE '% WHERE %'"
                )
            }
            walks = {
                statement: detail
                for statement, parameters in read
                for *_, detail in connection.execute(
                    f"EXPLAIN QUERY PLAN {statement}", parameters
                )
                if detail.startswith("SCAN")
                and "workflow_status" in detail
                and not any(f"INDEX {name}" in detail for name in partial)
            }
        assert walks == {}


def _recover_legacy_job(harness):
    from datetime import timedelta

    from app.core.config import settings
    from app.core.time import utcnow
    from app.modules.work.reconciler import PassResult, _repair

    # Legacy persisted arguments cannot prove current execution ownership.
    DBOS.retrieve_workflow("dbos-231-job:1").get_result()
    assert harness.state("dbos-231-job") is JobState.QUEUED
    assert RECORD.steps == []
    result = PassResult()
    _repair(
        harness.catalog.definition(PLAIN),
        now=utcnow() + timedelta(seconds=settings.jobs_submit_grace_seconds + 1),
        result=result,
    )
    assert result.submitted == 1
    harness.settle()


@pytest.mark.postgres
class TestPostgresReset:
    def test_preserves_application_data(self):
        url, schema = system_database_url(fresh_postgres_database("reset_scope"))
        database = create_engine(url)
        # The engine shares a PostgreSQL database with application tables.
        # This independent table represents persisted application data outside
        # its disposable schema, without importing another domain's entities.
        application = Table(
            "application_reset_probe",
            MetaData(),
            Column("id", Integer, primary_key=True),
            schema="public",
        )
        try:
            application.create(database)
            with database.begin() as connection:
                connection.execute(application.insert().values(id=7))
            with running_engine((url, schema)) as harness:
                harness.engine.reset()
                with database.connect() as connection:
                    assert connection.execute(application.select()).scalars().all() == [
                        7
                    ]
                harness.engine.launch(listen_lanes=[])
        finally:
            database.dispose()


class TestSdkUpgrade:
    def test_saved_result_remains_readable(self, upgraded):
        assert DBOS.retrieve_workflow("dbos-231-result:1").get_result() == {
            "job_id": "dbos-231-result",
            "attempt": 1,
        }

    def test_saved_queued_job_recovers_with_current_execution_authority(self, upgraded):
        upgraded.relaunch(app_version=VERSION, listen_lanes=None)
        _recover_legacy_job(upgraded)

        assert upgraded.state("dbos-231-job") == JobState.COMPLETED
        assert RECORD.steps == [
            ("upgrade/queued", "first"),
            ("upgrade/queued", "second"),
        ]

    def test_replayed_id_preserves_the_saved_input(self, upgraded):
        outcome = upgraded.engine.submit(
            JobSubmission(
                execution_id="dbos-231-job:1",
                job_id="wrong-input",
                attempt=1,
                definition=PLAIN,
                subject_key="wrong-subject",
                routing=Deduplicated("wrong-input"),
                lane=upgraded.catalog.definitions[PLAIN].lane,
                priority=WorkPriority.INTERACTIVE,
                execution_epoch="test-epoch",
            )
        )
        assert outcome == SubmitOutcome.EXISTING
        upgraded.relaunch(app_version=VERSION, listen_lanes=None)
        _recover_legacy_job(upgraded)

        assert upgraded.state("dbos-231-job") == JobState.COMPLETED
        assert RECORD.firsts() == ["upgrade/queued"]


@contextmanager
def ordered_engine(kind, tmp_path, lane_name, order):
    original = contract_catalog()
    lanes = dict(original.lanes)
    lanes[lane_name] = replace(lanes[lane_name], concurrency=1, queue_order=order)
    definitions = [
        replace(definition, lane=lane_name) if definition.name == PLAIN else definition
        for definition in original.definitions.values()
    ]
    catalog = WorkCatalog(definitions, lanes=lanes)
    RECORD.steps.clear()
    RECORD.behaviour.clear()
    RECORD.discover.clear()
    RECORD.running = RECORD.peak = 0
    with shared_app_db(tmp_path / "application.sqlite"):
        harness = (
            InlineHarness(catalog)
            if kind == "inline"
            else DbosHarness(catalog, f"sqlite:///{tmp_path / 'engine.sqlite'}")
        )
        catalog_module.bind(harness.engine, catalog)
        try:
            yield harness
        finally:
            for behaviour in RECORD.behaviour.values():
                gate = getattr(behaviour, "gate", None)
                if gate is not None:
                    gate.set()
            harness.close()
            catalog_module.bind(None, None)


class TestQueueOrder:
    @pytest.mark.parametrize("kind", ["inline", "dbos"])
    @pytest.mark.parametrize("lane", [LaneName.DERIVE_NATIVE, LaneName.DERIVE_LIGHT])
    def test_fifo_derivation_dispatches_backfill_before_later_interactive(
        self, kind, lane, tmp_path
    ):
        from app.modules.work.contracts import LaneOrder

        with ordered_engine(kind, tmp_path, lane, LaneOrder.FIFO) as harness:
            gate = threading.Event()
            blocker = harness.job(PLAIN, "fifo/blocker", behaviour=gated(gate))
            submission.submit(blocker)
            harness.started("fifo/blocker")
            backfill = harness.job(
                PLAIN, "fifo/backfill", priority=WorkPriority.BACKFILL
            )
            submission.submit(backfill)
            interactive = harness.job(PLAIN, "fifo/interactive")
            submission.submit(interactive)
            depth = harness.engine.lane_depth(lane)
            assert depth.queued + depth.running == 3
            gate.set()
            harness.settle()

            order = RECORD.firsts()
            assert order.index("fifo/backfill") < order.index("fifo/interactive")
            assert RECORD.peak == 1
            depth = harness.engine.lane_depth(lane)
            assert (depth.queued, depth.running) == (0, 0)
            with get_session_factory().scoped_session() as session:
                rows = session.exec(
                    select(Job).where(Job.id.in_([backfill, interactive]))
                ).all()
                assert {row.id: row.priority for row in rows} == {
                    backfill: WorkPriority.BACKFILL,
                    interactive: WorkPriority.INTERACTIVE,
                }
                assert all(row.state == JobState.COMPLETED for row in rows)
                assert all(row.attempts == 1 for row in rows)

    @pytest.mark.parametrize("kind", ["inline", "dbos"])
    def test_priority_lane_dispatches_interactive_before_queued_backfill(
        self, kind, tmp_path
    ):
        from app.modules.work.contracts import LaneOrder

        with ordered_engine(
            kind, tmp_path, LaneName.MAINTENANCE, LaneOrder.PRIORITY
        ) as harness:
            gate = threading.Event()
            blocker = harness.job(PLAIN, "priority/blocker", behaviour=gated(gate))
            submission.submit(blocker)
            harness.started("priority/blocker")
            backfill = harness.job(
                PLAIN, "priority/backfill", priority=WorkPriority.BACKFILL
            )
            submission.submit(backfill)
            interactive = harness.job(PLAIN, "priority/interactive")
            submission.submit(interactive)
            gate.set()
            harness.settle()

            order = RECORD.firsts()
            assert order.index("priority/interactive") < order.index(
                "priority/backfill"
            )
            assert RECORD.peak == 1


class TestQueueTransition:
    @pytest.mark.parametrize("lane", [LaneName.DERIVE_NATIVE, LaneName.DERIVE_LIGHT])
    def test_re_ranks_legacy_backfill_before_consumption(self, lane, tmp_path):
        from dbos import DBOSClient

        from app.modules.work.contracts import LaneOrder

        original = contract_catalog()
        lanes = dict(original.lanes)
        lanes[lane] = replace(
            lanes[lane], concurrency=1, queue_order=LaneOrder.PRIORITY
        )
        catalog = WorkCatalog(
            [
                replace(definition, lane=lane)
                if definition.name == PLAIN
                else definition
                for definition in original.definitions.values()
            ],
            lanes=lanes,
        )
        RECORD.steps.clear()
        RECORD.behaviour.clear()
        RECORD.discover.clear()
        RECORD.running = RECORD.peak = 0
        url = f"sqlite:///{tmp_path / 'legacy.sqlite'}"
        with shared_app_db(tmp_path / "application.sqlite"):
            harness = DbosHarness(catalog, url, listen_lanes=[])
            catalog_module.bind(harness.engine, catalog)
            client = DBOSClient(
                system_database_url=url,
                dbos_system_schema=None,
                lazy=True,
                retry_connection_errors=False,
                observability_query_timeout_sec=5,
            )
            try:
                job_id = harness.job(
                    PLAIN, "transition/backfill", priority=WorkPriority.BACKFILL
                )
                submission.submit(job_id)
                saved = client.list_workflows(queue_name=lane.value, status="ENQUEUED")
                assert len(saved) == 1
                assert saved[0].priority == 1000
                harness.engine.shutdown()
                catalog.lanes[lane] = replace(
                    catalog.lanes[lane], queue_order=LaneOrder.FIFO
                )

                harness.engine.launch(listen_lanes=[])

                assert RECORD.steps == []
                remaining = client.list_workflows(
                    queue_name=lane.value, status="ENQUEUED"
                )
                assert [(row.workflow_id, row.priority) for row in remaining] == [
                    (saved[0].workflow_id, 1)
                ]
                with get_session_factory().scoped_session() as session:
                    job = session.get(Job, job_id)
                    assert job is not None
                    assert job.state == JobState.QUEUED
                    assert job.priority == WorkPriority.BACKFILL
                    assert job.attempts == 1
            finally:
                client.destroy()
                harness.close()
                catalog_module.bind(None, None)

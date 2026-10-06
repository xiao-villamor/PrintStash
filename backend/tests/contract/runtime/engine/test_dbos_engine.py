"""Actual engine queue ordering, persistent ticks and DBOS system-state upgrades."""

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from dbos import DBOS
from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlmodel import select

from app.db.models import Job, JobState, LaneName, WorkPriority
from app.db.session import get_session_factory
from app.modules.work import catalog as catalog_module
from app.modules.work import submission
from app.modules.work.catalog import WorkCatalog
from app.modules.work.contracts import Deduplicated, JobSubmission, SubmitOutcome
from app.runtime.engine.dbos_engine import TICK_WORKFLOW, system_database_url
from tests.containers import fresh_postgres_database
from tests.contract.modules.work._harness import (
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
    def test_refuses_legacy_backfill_before_consumption(self, lane, tmp_path):
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

                with pytest.raises(
                    RuntimeError, match="derivation_queue_requires_drain"
                ):
                    harness.engine.launch(listen_lanes=[lane])

                assert RECORD.steps == []
                remaining = client.list_workflows(
                    queue_name=lane.value, status="ENQUEUED"
                )
                assert [(row.workflow_id, row.priority) for row in remaining] == [
                    (saved[0].workflow_id, 1000)
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

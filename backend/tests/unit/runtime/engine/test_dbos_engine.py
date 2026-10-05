"""The DBOS adapter's translations, without launching DBOS.

How a lane becomes a queue (which concurrency bound, which limiter), where the
system database lives for each application database, how the tick interval
becomes a cron, and how a step that exhausted its retries reports the step's
own error rather than DBOS's wrapper. The engine running for real is the port
contract suite in ``tests/contract/modules/work``.
"""

from __future__ import annotations

import asyncio

import pytest
from dbos import DBOS
from dbos import error as dbos_error

from app.db.models import LaneName
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.modules.work.contracts import Lane, RetryPolicy
from app.runtime.engine import dbos_engine
from app.runtime.engine.dbos_engine import DbosJobEngine, system_database_url


def _engine(*lanes: Lane, schema: str | None = None) -> DbosJobEngine:
    return DbosJobEngine(
        WorkCatalog(lanes={**default_lanes(), **{lane.name: lane for lane in lanes}}),
        system_database_url="sqlite:////data/db/printstash-dbos.sqlite",
        schema=schema,
        executor_id="api-test",
        app_version="9.9.9",
    )


class TestSystemDatabase:
    def test_sqlite_uses_a_sibling_file(self) -> None:
        assert system_database_url("sqlite:////data/db/printstash.db") == (
            "sqlite:////data/db/printstash-dbos.db",
            None,
        )

    def test_a_sqlite_file_without_a_suffix_gets_one(self) -> None:
        url, _ = system_database_url("sqlite:////data/db/vault")

        assert url == "sqlite:////data/db/vault-dbos.sqlite"

    @pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
    def test_an_in_memory_database_is_refused(self, url: str) -> None:
        with pytest.raises(ValueError, match="file_backed_sqlite"):
            system_database_url(url)

    def test_postgres_uses_the_dbos_schema_of_the_same_database(self) -> None:
        url, schema = system_database_url("postgresql://vault:pw@db:5432/vault")

        assert (url, schema) == (
            "postgresql+psycopg://vault:pw@db:5432/vault",
            "dbos",
        )


class TestConfig:
    def test_sqlite_waits_out_a_locked_file(self) -> None:
        config = _engine()._config()

        assert config["db_engine_kwargs"] == {"connect_args": {"timeout": 30}}
        assert "dbos_system_schema" not in config

    def test_postgres_names_its_schema(self) -> None:
        config = _engine(schema="dbos")._config()

        assert config["dbos_system_schema"] == "dbos"
        assert "db_engine_kwargs" not in config

    def test_identifies_this_build_to_the_engine(self) -> None:
        config = _engine()._config()

        assert (config["application_version"], config["executor_id"]) == (
            "9.9.9",
            "api-test",
        )


@pytest.fixture
def registered(monkeypatch) -> dict[str, dict]:
    queues: dict[str, dict] = {}

    def register_queue(name: str, **options):
        queues[name] = options
        return name

    monkeypatch.setattr(DBOS, "register_queue", register_queue)
    return queues


class TestQueues:
    def test_a_worker_lane_bounds_each_process(self, registered) -> None:
        _engine()._register_queue(Lane(LaneName.INGEST, 2))

        assert registered["ingest"]["worker_concurrency"] == 2

    def test_a_global_lane_bounds_the_deployment(self, registered) -> None:
        _engine()._register_queue(Lane(LaneName.SIMILARITY, 1, scope="global"))

        assert registered["similarity"]["global_concurrency"] == 1

    def test_a_partitioned_lane_limits_each_partition(self, registered) -> None:
        _engine()._register_queue(
            Lane(LaneName.NOTIFY, 1, partitioned=True, rate_limit=(30, 60.0))
        )

        options = registered["notify"]
        assert (options["partition_concurrency"], options["partition_limiter"]) == (
            1,
            {"limit": 30, "period": 60.0},
        )
        assert "limiter" not in options

    def test_a_rate_limited_lane_is_limited_as_a_whole(self, registered) -> None:
        _engine()._register_queue(Lane(LaneName.NETWORK, 4, rate_limit=(10, 1.0)))

        assert registered["network"]["limiter"] == {"limit": 10, "period": 1.0}


class _Queue:
    def __init__(self) -> None:
        self.set: list[tuple[str, int]] = []

    def set_partition_concurrency(self, value: int) -> None:
        self.set.append(("partition", value))

    def set_global_concurrency(self, value: int) -> None:
        self.set.append(("global", value))

    def set_worker_concurrency(self, value: int) -> None:
        self.set.append(("worker", value))


class TestLaneConcurrency:
    @pytest.mark.parametrize(
        ("lane", "bound"),
        [
            (Lane(LaneName.INGEST, 2), "worker"),
            (Lane(LaneName.SIMILARITY, 1, scope="global"), "global"),
            (Lane(LaneName.PRINTING, 1, partitioned=True), "partition"),
        ],
    )
    def test_an_override_moves_the_lanes_own_bound(
        self, lane: Lane, bound: str
    ) -> None:
        engine = _engine(lane)
        queue = _Queue()
        engine._queues[lane.name] = queue  # type: ignore[assignment]

        engine.set_lane_concurrency(lane.name, 5)

        assert queue.set == [(bound, 5)]

    def test_a_lane_without_a_queue_is_a_bug(self) -> None:
        # Every lane is registered at launch; an unlaunched engine has none.
        with pytest.raises(KeyError):
            _engine().set_lane_concurrency(LaneName.INGEST, 5)


class TestTickCron:
    def test_whole_minutes_use_a_minute_cron(self) -> None:
        assert dbos_engine._tick_cron(300) == "*/5 * * * *"

    def test_seconds_use_a_seconds_cron(self) -> None:
        assert dbos_engine._tick_cron(15) == "*/15 * * * * *"

    def test_an_odd_interval_is_capped_at_a_minute(self) -> None:
        assert dbos_engine._tick_cron(90) == "*/59 * * * * *"


class TestRunner:
    def test_exhausted_retries_raise_the_steps_own_error(self, monkeypatch) -> None:
        cause = ConnectionError("printer offline")

        def run_step(_options, _fn):
            raise dbos_error.DBOSMaxStepRetriesExceeded("step", 3, [cause])

        monkeypatch.setattr(DBOS, "run_step", run_step)

        with pytest.raises(ConnectionError) as raised:
            dbos_engine._DbosRunner().run(
                "step", lambda: None, RetryPolicy(max_attempts=3)
            )

        assert raised.value is cause

    def test_exhausted_retries_without_errors_raise_the_wrapper(
        self, monkeypatch
    ) -> None:
        def run_step(_options, _fn):
            raise dbos_error.DBOSMaxStepRetriesExceeded("step", 3, [])

        monkeypatch.setattr(DBOS, "run_step", run_step)

        with pytest.raises(dbos_error.DBOSMaxStepRetriesExceeded):
            dbos_engine._DbosRunner().run(
                "step", lambda: None, RetryPolicy(max_attempts=3)
            )

    def test_a_single_attempt_step_asks_for_no_retries(self, monkeypatch) -> None:
        seen: list[dict] = []
        monkeypatch.setattr(
            DBOS, "run_step", lambda options, fn: seen.append(options) or fn()
        )

        assert dbos_engine._DbosRunner().run("step", lambda: 7, RetryPolicy()) == 7
        assert seen == [{"name": "step"}]

    def test_recognises_a_cancelled_workflow(self) -> None:
        runner = dbos_engine._DbosRunner()

        assert runner.is_cancellation(
            dbos_error.DBOSWorkflowCancelledError("workflow-1")
        )
        assert not runner.is_cancellation(RuntimeError("boom"))


class TestDetached:
    def test_a_call_from_the_event_loop_runs_on_another_thread(self) -> None:
        import threading

        async def from_loop() -> tuple[int, int]:
            return threading.get_ident(), dbos_engine._detached(threading.get_ident)

        loop_thread, called_on = asyncio.run(from_loop())

        assert loop_thread != called_on

    def test_a_call_outside_any_workflow_runs_in_place(self) -> None:
        import threading

        assert dbos_engine._detached(threading.get_ident) == threading.get_ident()


class TestFifoQueueConfiguration:
    @pytest.mark.parametrize(
        ("scope", "partitioned", "bound"),
        [
            ("worker", False, "worker_concurrency"),
            ("global", False, "global_concurrency"),
            ("worker", True, "partition_concurrency"),
        ],
    )
    def test_fifo_preserves_one_queue_with_its_configured_limits(
        self, registered, scope, partitioned, bound
    ):
        from app.modules.work.contracts import LaneOrder

        lane = Lane(
            LaneName.DERIVE_NATIVE,
            3,
            scope=scope,
            partitioned=partitioned,
            rate_limit=(10, 60.0),
            queue_order=LaneOrder.FIFO,
        )
        _engine()._register_queue(lane)

        assert set(registered) == {LaneName.DERIVE_NATIVE.value}
        options = registered[LaneName.DERIVE_NATIVE.value]
        assert options[bound] == 3
        limiter = "partition_limiter" if partitioned else "limiter"
        assert options[limiter] == {"limit": 10, "period": 60.0}
        assert options["on_conflict"] == "always_update"


class TestFifoStartup:
    @pytest.mark.parametrize("existing_empty", [False, True])
    def test_a_fresh_sqlite_database_needs_no_legacy_inspection(
        self, tmp_path, monkeypatch, existing_empty
    ):
        from app.modules.work.contracts import LaneOrder

        path = tmp_path / "fresh.sqlite"
        if existing_empty:
            path.touch()
        engine = _engine(Lane(LaneName.DERIVE_NATIVE, 1, queue_order=LaneOrder.FIFO))
        engine.url = f"sqlite:///{path}"

        def forbidden_client(**kwargs):
            raise AssertionError("fresh database has no persisted queue")

        monkeypatch.setattr(dbos_engine, "DBOSClient", forbidden_client)
        engine._require_fifo_queues_drained()

    @pytest.mark.parametrize("case", ["legacy", "overflow", "query_failure"])
    def test_unverified_persisted_queue_fails_closed(self, tmp_path, monkeypatch, case):
        from types import SimpleNamespace

        from app.modules.work.contracts import LaneOrder

        path = tmp_path / "existing.sqlite"
        path.write_bytes(b"existing system database")
        engine = _engine(Lane(LaneName.DERIVE_NATIVE, 1, queue_order=LaneOrder.FIFO))
        engine.url = f"sqlite:///{path}"
        destroyed = []
        calls = []

        class Client:
            def __init__(self, **options):
                assert options["lazy"] is True
                assert options["retry_connection_errors"] is False
                assert options["observability_query_timeout_sec"] == 5

            def list_workflows(self, **options):
                calls.append(options)
                assert options["limit"] <= 100
                assert options["load_input"] is False
                assert options["load_output"] is False
                if case == "query_failure":
                    raise OSError("corrupt system database")
                if case == "legacy":
                    return [SimpleNamespace(priority=1000)]
                return [SimpleNamespace(priority=1) for _ in range(options["limit"])]

            def destroy(self):
                destroyed.append(True)

        monkeypatch.setattr(dbos_engine, "DBOSClient", Client)

        with pytest.raises(RuntimeError, match="derivation_queue_requires_drain"):
            engine._require_fifo_queues_drained()

        assert destroyed == [True]
        assert len(calls) <= 5

    def test_neutral_persisted_queue_is_inspected_without_payloads(
        self, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace

        from app.modules.work.contracts import LaneOrder

        path = tmp_path / "existing.sqlite"
        path.write_bytes(b"existing system database")
        engine = _engine(Lane(LaneName.DERIVE_NATIVE, 1, queue_order=LaneOrder.FIFO))
        engine.url = f"sqlite:///{path}"
        destroyed = []
        calls = []

        class Client:
            def __init__(self, **options):
                assert options["system_database_url"] == engine.url

            def list_workflows(self, **options):
                calls.append(options)
                return [SimpleNamespace(priority=1)]

            def destroy(self):
                destroyed.append(True)

        monkeypatch.setattr(dbos_engine, "DBOSClient", Client)
        engine._require_fifo_queues_drained()

        assert destroyed == [True]
        assert {call["queue_name"] for call in calls} == {
            LaneName.DERIVE_NATIVE.value,
            LaneName.DERIVE_LIGHT.value,
        }
        assert all(
            call["status"] == ["ENQUEUED", "DELAYED", "PENDING"] for call in calls
        )
        assert all(
            call["load_input"] is False and call["load_output"] is False
            for call in calls
        )


class TestFifoPostgresStartup:
    def test_an_absent_schema_is_fresh(self, monkeypatch):
        engine = _engine(schema="dbos")
        engine.url = "postgresql+psycopg://vault:pw@localhost/vault"
        disposed = []
        inspected = []

        class Database:
            def dispose(self):
                disposed.append(True)

        database = Database()

        def create(url, **options):
            assert url == engine.url
            assert options["connect_args"]["connect_timeout"] == 5
            return database

        class Inspector:
            def has_schema(self, schema):
                inspected.append(schema)
                return False

        monkeypatch.setattr(dbos_engine, "create_engine", create)
        monkeypatch.setattr(dbos_engine, "inspect", lambda value: Inspector())

        def forbidden_client(**kwargs):
            raise AssertionError("absent schema has no persisted queues")

        monkeypatch.setattr(dbos_engine, "DBOSClient", forbidden_client)
        engine._require_fifo_queues_drained()

        assert inspected == ["dbos"]
        assert disposed == [True]

    def test_schema_inspection_failure_fails_closed(self, monkeypatch):
        engine = _engine(schema="dbos")
        engine.url = "postgresql+psycopg://vault:pw@localhost/vault"
        disposed = []

        class Database:
            def dispose(self):
                disposed.append(True)

        class Inspector:
            def has_schema(self, schema):
                raise OSError("database unavailable")

        monkeypatch.setattr(
            dbos_engine, "create_engine", lambda *args, **kw: Database()
        )
        monkeypatch.setattr(dbos_engine, "inspect", lambda value: Inspector())

        with pytest.raises(RuntimeError, match="derivation_queue_requires_drain"):
            engine._require_fifo_queues_drained()

        assert disposed == [True]

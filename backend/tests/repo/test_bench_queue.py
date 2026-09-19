"""Queue baseline inputs are bounded before creating databases or processes."""

import pytest
from sqlalchemy import create_engine

from scripts.bench_queue import _require_owned_database, database_bytes, run


class TestQueueBenchmarkBounds:
    @pytest.mark.parametrize(
        ("count", "idle_seconds"),
        [(0, 1), (1025, 1), (1, 0), (1, 61), (1, float("nan"))],
        ids=[
            "no-jobs",
            "too-many-jobs",
            "no-idle-sample",
            "unbounded-idle",
            "nan-idle",
        ],
    )
    def test_refuses_unbounded_work_before_creating_output(
        self, tmp_path, count, idle_seconds
    ):
        output = tmp_path / "absent" / "queue.json"

        with pytest.raises(ValueError, match="workload exceeds its bounds"):
            run(output, database="sqlite", count=count, idle_seconds=idle_seconds)

        assert not output.parent.exists()


class TestQueueDatabaseMeasurement:
    def test_measures_real_sqlite_database_bytes(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'queue.sqlite'}")
        before = database_bytes(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE queue_probe (value TEXT NOT NULL)")
            connection.exec_driver_sql(
                "INSERT INTO queue_probe (value) VALUES (?)", ("measured",)
            )

        assert before >= 0
        assert database_bytes(engine) > before
        engine.dispose()


class TestQueueBenchmarkOwnership:
    def test_refuses_internal_execution_without_a_disposable_owner(self, monkeypatch):
        monkeypatch.delenv("PRINTSTASH_QUEUE_BENCHMARK_OWNER", raising=False)

        with pytest.raises(RuntimeError, match="disposable-database supervisor"):
            _require_owned_database()

    def test_refuses_a_changed_database(self, monkeypatch, tmp_path):
        owner = tmp_path / "owner"
        owner.write_text("different database")
        monkeypatch.setenv("PRINTSTASH_QUEUE_BENCHMARK_OWNER", str(owner))
        monkeypatch.setenv("VAULT_DB_URL", "sqlite:///changed.sqlite")

        with pytest.raises(RuntimeError, match="ownership does not match"):
            _require_owned_database()

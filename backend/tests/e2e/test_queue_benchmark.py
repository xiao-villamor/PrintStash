"""The baseline diagnostic must recover the same real job after process death."""

import json
import signal

import pytest

from scripts.bench_queue import run


class TestQueueBenchmark:
    @pytest.mark.parametrize(
        "database", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]
    )
    def test_recovers_a_terminated_worker_without_losing_jobs(self, tmp_path, database):
        output = tmp_path / "queue.json"

        report = run(output, database=database, count=3, idle_seconds=1)

        assert report["accepted_count"] == report["completed_count"] == 4
        assert report["rollback_orphans"] == 0
        assert report["duplicate_claims"] == 0
        assert report["stale_completion_rejected"] is True
        assert report["terminated_worker_exit_code"] == -signal.SIGKILL
        assert report["recovery_seconds"] > 0
        assert report["claim"]["count"] == 3
        assert report["enqueue_to_start"]["count"] == 3
        assert report["database_growth_bytes"] > 0
        assert json.loads(output.read_text()) == report

    @pytest.mark.parametrize(
        "database", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]
    )
    def test_records_only_verified_steady_state_operations(self, tmp_path, database):
        report = run(
            tmp_path / "steady.json",
            database=database,
            count=3,
            idle_seconds=1,
            include_recovery=False,
        )
        assert report["measurement_protocol"] == "durable-queue-steady-v1"
        assert report["accepted_count"] == report["completed_count"] == 3
        assert report["rollback_orphans"] == report["duplicate_claims"] == 0
        assert report["fault_injection"] == "not_run"
        assert "recovery_seconds" not in report
        assert "stale_completion_rejected" not in report
        assert report["claim"]["count"] == 3
        assert report["enqueue_to_start"]["count"] == 3
        assert report["database_growth_bytes"] > 0

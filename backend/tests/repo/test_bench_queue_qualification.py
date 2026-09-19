"""The queue-candidate comparison rejects incomplete or unbounded evidence."""

import pytest

from scripts.bench_queue_qualification import (
    candidate_metrics,
    current_metrics,
    execution_order,
    summarize,
    summarize_completed_boundary,
    validate_profile,
)


def latency(value: float, count: int = 8) -> dict:
    return {"count": count, "p50_ms": value / 2, "p95_ms": value, "max_ms": value * 2}


def current_report(**overrides) -> dict:
    report = {
        "measurement_protocol": "durable-queue-steady-v1",
        "accepted_count": 8,
        "completed_count": 8,
        "duplicate_claims": 0,
        "acceptance": latency(2),
        "claim": latency(3),
        "enqueue_to_start": latency(5),
        "completion": latency(4),
        "idle": {"seconds": 2, "cpu_seconds": 0.05},
        "total_seconds": 6,
        "container_cpu_seconds": 1,
        "container_memory_peak_bytes": 1024,
        "database_growth_bytes": 512,
    }
    report.update(overrides)
    return report


def candidate_report(**overrides) -> dict:
    report = {
        "measurement_protocol": "queue-candidate-v1",
        "jobs": 8,
        "accepted_count": 8,
        "completed_count": 8,
        "duplicate_executions": 0,
        "enqueue": latency(2),
        "enqueue_to_start": latency(3),
        "acknowledgment": latency(4),
        "processing_seconds": 2,
        "throughput_jobs_per_second": 4,
        "idle_cpu_seconds": 0.04,
        "container_cpu_seconds": 1,
        "container_memory_peak_bytes": 1024,
        "database_growth_bytes": 512,
    }
    report.update(overrides)
    return report


class TestQueueQualificationComparison:
    def test_normalizes_current_queue_metrics(self):
        report = current_report()

        metrics = current_metrics(report)

        assert metrics["throughput_jobs_per_second"] == 2
        assert metrics["enqueue_p95_ms"] == 2
        assert metrics["start_p95_ms"] == 5
        assert metrics["acknowledgment_p95_ms"] == 4


    def test_rejects_candidate_count_mismatch(self):
        report = candidate_report()

        assert candidate_metrics(report)["throughput_jobs_per_second"] == 4
        report["completed_count"] = 7
        with pytest.raises(ValueError, match="counts or duplicate executions"):
            candidate_metrics(report)


    @pytest.mark.parametrize(
        ("cpus", "memory_gib", "pairs", "jobs"),
        [(1, 2, 7, 128), (2, 3, 7, 128), (2, 2, 8, 128), (4, 4, 7, 0)],
    )
    def test_rejects_non_protocol_profiles(self, cpus, memory_gib, pairs, jobs):
        with pytest.raises(ValueError, match="controlled queue profile"):
            validate_profile(cpus=cpus, memory_gib=memory_gib, pairs=pairs, jobs=jobs)


    def test_preserves_pair_identity_during_rotation(self):
        assert execution_order(0) == ("current", "apalis", "azums")
        assert execution_order(1) == ("apalis", "azums", "current")
        assert execution_order(2) == ("azums", "current", "apalis")
        assert execution_order(3) == execution_order(0)


    def test_summary_applies_metric_direction_to_review_thresholds(self):
        current = current_report()
        candidate = candidate_report()

        report = summarize(
            {
                "current": [current] * 7,
                "apalis": [candidate] * 7,
                "azums": [candidate] * 7,
            }
        )

        throughput = report["metrics"]["throughput_jobs_per_second"]
        assert report["noisy"] is False
        assert throughput["direction"] == "higher_is_better"
        assert throughput["implementations_over_threshold"] == []

        noisy = summarize(
            {
                "current": [current] * 7,
                "apalis": [
                    {**candidate, "throughput_jobs_per_second": value}
                    for value in (1, 8, 1, 8, 1, 8, 1)
                ],
                "azums": [candidate] * 7,
            }
        )
        assert noisy["noisy"] is True


    def test_noisy_comparison_uses_seven_or_fourteen_pairs(self):
        current = current_report()
        candidate = candidate_report()

        runs = {
            "current": [current] * 7,
            "apalis": [
                {**candidate, "throughput_jobs_per_second": value}
                for value in (1, 8, 1, 8, 1, 8, 1)
            ],
            "azums": [candidate] * 7,
        }
        boundary = summarize_completed_boundary(runs)
        assert boundary is not None
        assert boundary["pairs"] == 7
        assert boundary["noisy"] is True

        for values, report in (
            (runs["current"], current),
            (runs["apalis"], candidate),
            (runs["azums"], candidate),
        ):
            values.append(report)
        assert summarize_completed_boundary(runs) is None

        for values, report in (
            (runs["current"], current),
            (runs["apalis"], candidate),
            (runs["azums"], candidate),
        ):
            values.extend([report] * 6)
        assert summarize_completed_boundary(runs)["pairs"] == 14

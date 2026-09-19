#!/usr/bin/env python3
"""Build comparable summaries for the current queue and Rust candidates.

The workflow owns Docker resource isolation and adds resource/database counters
to each raw report. This module validates correctness fields before comparing
timings, rotates execution order to limit drift, and never retries a failed
candidate operation.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Mapping
from pathlib import Path

IMPLEMENTATIONS = ("current", "apalis", "azums")
RESOURCE_FIELDS = (
    "container_cpu_seconds",
    "container_memory_peak_bytes",
    "database_growth_bytes",
)
HIGHER_IS_BETTER = {"throughput_jobs_per_second"}


def validate_profile(*, cpus: int, memory_gib: int, pairs: int, jobs: int) -> None:
    if (
        (cpus, memory_gib) not in {(2, 2), (4, 4)}
        or pairs not in {7, 14}
        or not 1 <= jobs <= 10_000
    ):
        raise ValueError("unsupported controlled queue profile")


def execution_order(index: int) -> tuple[str, str, str]:
    if index < 0:
        raise ValueError("execution index cannot be negative")
    offset = index % len(IMPLEMENTATIONS)
    return IMPLEMENTATIONS[offset:] + IMPLEMENTATIONS[:offset]


def _number(report: Mapping, key: str) -> float:
    value = report.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"missing numeric queue metric: {key}")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid queue metric: {key}")
    return value


def _latency(report: Mapping, key: str) -> float:
    value = report.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"missing latency summary: {key}")
    count = _number(value, "count")
    p95 = _number(value, "p95_ms")
    if count < 1:
        raise ValueError(f"empty latency summary: {key}")
    return p95


def _resources(report: Mapping) -> dict[str, float]:
    return {key: _number(report, key) for key in RESOURCE_FIELDS}


def current_metrics(report: Mapping) -> dict[str, float]:
    if report.get("measurement_protocol") != "durable-queue-steady-v1":
        raise ValueError("current queue evidence uses an unexpected protocol")
    accepted = _number(report, "accepted_count")
    completed = _number(report, "completed_count")
    if accepted != completed or _number(report, "duplicate_claims") != 0:
        raise ValueError("current queue counts or duplicate claims changed")
    idle = report.get("idle")
    if not isinstance(idle, Mapping):
        raise TypeError("current queue idle evidence is missing")
    processing = _number(report, "total_seconds") - _number(idle, "seconds")
    if processing <= 0:
        raise ValueError("current queue processing interval is invalid")
    return {
        "throughput_jobs_per_second": completed / processing,
        "enqueue_p95_ms": _latency(report, "acceptance"),
        "start_p95_ms": _latency(report, "enqueue_to_start"),
        "acknowledgment_p95_ms": _latency(report, "completion"),
        "idle_cpu_seconds": _number(idle, "cpu_seconds"),
        **_resources(report),
    }


def candidate_metrics(report: Mapping) -> dict[str, float]:
    if report.get("measurement_protocol") != "queue-candidate-v1":
        raise ValueError("candidate evidence uses an unexpected protocol")
    jobs = _number(report, "jobs")
    if (
        _number(report, "accepted_count") != jobs
        or _number(report, "completed_count") != jobs
        or _number(report, "duplicate_executions") != 0
    ):
        raise ValueError("candidate counts or duplicate executions changed")
    return {
        "throughput_jobs_per_second": _number(
            report, "throughput_jobs_per_second"
        ),
        "enqueue_p95_ms": _latency(report, "enqueue"),
        "start_p95_ms": _latency(report, "enqueue_to_start"),
        "acknowledgment_p95_ms": _latency(report, "acknowledgment"),
        "idle_cpu_seconds": _number(report, "idle_cpu_seconds"),
        **_resources(report),
    }


def summarize(runs: Mapping[str, list[Mapping]]) -> dict:
    if set(runs) != set(IMPLEMENTATIONS):
        raise ValueError("queue comparison requires all three implementations")
    pair_count = {len(values) for values in runs.values()}
    if len(pair_count) != 1 or pair_count.pop() not in {7, 14}:
        raise ValueError("queue comparison requires seven or fourteen complete pairs")
    normalized = {
        name: [
            (current_metrics if name == "current" else candidate_metrics)(report)
            for report in reports
        ]
        for name, reports in runs.items()
    }
    metrics = {}
    noisy = False
    for name in normalized["current"][0]:
        baseline = [run[name] for run in normalized["current"]]
        threshold = 10 if name.endswith(("seconds", "bytes")) else 5
        if any(value == 0 for value in baseline):
            raise ValueError(f"current queue metric cannot be zero: {name}")
        variations = {
            implementation: (
                statistics.stdev(run[name] for run in values)
                / statistics.mean(run[name] for run in values)
                * 100
                if len(values) > 1 and statistics.mean(run[name] for run in values)
                else 0
            )
            for implementation, values in normalized.items()
        }
        noisy |= max(variations.values()) > threshold
        metrics[name] = {
            implementation: {
                "median": statistics.median(run[name] for run in values),
                "paired_delta_from_current_percent": statistics.median(
                    100 * (candidate[name] / current - 1)
                    for current, candidate in zip(
                        baseline, values, strict=True
                    )
                    if current
                ),
                "coefficient_of_variation_percent": variations[implementation],
            }
            for implementation, values in normalized.items()
        }
        metrics[name]["review_threshold_percent"] = threshold
        metrics[name]["direction"] = (
            "higher_is_better" if name in HIGHER_IS_BETTER else "lower_is_better"
        )
        metrics[name]["implementations_over_threshold"] = [
            implementation
            for implementation in ("apalis", "azums")
            if (
                metrics[name][implementation]["paired_delta_from_current_percent"]
                < -threshold
                if name in HIGHER_IS_BETTER
                else metrics[name][implementation][
                    "paired_delta_from_current_percent"
                ]
                > threshold
            )
        ]
    return {
        "schema_version": 1,
        "pairs": len(baseline),
        "noisy": noisy,
        "metrics": metrics,
    }


def summarize_completed_boundary(runs: Mapping[str, list[Mapping]]) -> dict | None:
    """Summarize only the protocol's seven- or fourteen-pair boundaries."""
    if set(runs) != set(IMPLEMENTATIONS):
        raise ValueError("queue comparison requires all three implementations")
    pair_counts = {len(values) for values in runs.values()}
    if len(pair_counts) != 1:
        raise ValueError("queue comparison pair counts diverged")
    pair_count = pair_counts.pop()
    if not 0 <= pair_count <= 14:
        raise ValueError("queue comparison exceeds fourteen pairs")
    return summarize(runs) if pair_count in {7, 14} else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for implementation in IMPLEMENTATIONS:
        parser.add_argument(
            f"--{implementation}",
            type=Path,
            action="append",
            required=True,
            help=f"One measured {implementation} JSON report; repeat seven or fourteen times",
        )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = {
        implementation: [
            json.loads(path.read_text()) for path in getattr(args, implementation)
        ]
        for implementation in IMPLEMENTATIONS
    }
    report = summarize(runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Measure the public G-code parser with deterministic text and BGCODE inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
import time
from pathlib import Path

import psutil
from printstash_core.gcode import parse

DEFAULT_ITERATIONS = 1_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("A latency percentile requires measured values")
    index = math.ceil(percentile * len(values)) - 1
    return sorted(values)[max(0, index)]


def _large_text(source: Path, destination: Path) -> None:
    command_chunk = b"G1 X1.0 Y1.0 E0.1\n" * 4096
    with destination.open("wb") as output:
        output.write(source.read_bytes())
        for _ in range(256):
            output.write(command_chunk)
        output.write(b"; layer_height = 0.2\n")


def _measure(path: Path, iterations: int) -> tuple[dict[str, object], dict[str, float]]:
    expected = parse(path).to_legacy_dict()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        actual = parse(path).to_legacy_dict()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        if actual != expected:
            raise RuntimeError(
                f"G-code parser result changed within one run: {path.name}"
            )
    return expected, {
        "p50_ms": statistics.median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def run(fixtures: Path, iterations: int) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("Iterations must be positive")
    small = fixtures / "sample.gcode"
    binary = fixtures / "prusaslicer.bgcode"
    for path in (small, binary):
        if not path.is_file():
            raise FileNotFoundError(path)

    process = psutil.Process()
    with tempfile.TemporaryDirectory(prefix="printstash-gcode-benchmark-") as temporary:
        work = Path(temporary)
        large = work / "large.gcode"
        malformed = work / "malformed.bgcode"
        _large_text(small, large)
        malformed.write_bytes(b"GCDE\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        sources = {
            "small_text": small,
            "large_text": large,
            "official_bgcode": binary,
            "malformed_bgcode": malformed,
        }
        cpu_started = time.process_time()
        elapsed_started = time.perf_counter()
        metadata: dict[str, dict[str, object]] = {}
        workloads: dict[str, dict[str, float]] = {}
        peak_rss = process.memory_info().rss
        for name, path in sources.items():
            expected, latency = _measure(path, iterations)
            metadata[name] = expected
            workloads[name] = latency
            peak_rss = max(peak_rss, process.memory_info().rss)
        total_seconds = time.perf_counter() - elapsed_started
        process_cpu_seconds = time.process_time() - cpu_started
        all_p95 = [values["p95_ms"] for values in workloads.values()]
        report: dict[str, object] = {
            "measurement_protocol": "gcode-parse-v1",
            "iterations_per_workload": iterations,
            "total_seconds": total_seconds,
            "parse_latency": {"p95_ms": max(all_p95)},
            "process_cpu_seconds": process_cpu_seconds,
            "process_peak_rss_bytes": peak_rss,
            "source_hashes": {name: _sha256(path) for name, path in sources.items()},
            "source_sizes": {
                name: path.stat().st_size for name, path in sources.items()
            },
            "metadata_catalog": metadata,
            "workloads": workloads,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--database", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    report = run(args.fixtures, args.iterations)
    report["database_dialect"] = args.database
    if args.compare:
        reference = json.loads(args.compare.read_text())
        for key in ("measurement_protocol", "source_hashes", "metadata_catalog"):
            if reference.get(key) != report.get(key):
                raise ValueError(f"G-code benchmark correctness differs: {key}")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

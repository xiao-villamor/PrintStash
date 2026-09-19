"""Measure native ZIP inspection and bounded extraction with verified outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import tempfile
import time
import zipfile
from pathlib import Path

import psutil
from printstash_core.files import (
    ArchiveLimits,
    ArchivePolicyError,
    extract_selected,
    inspect_archive,
)

DEFAULT_ITERATIONS = 20
FILE_TYPES = {".stl": "stl", ".3mf": "3mf", ".gcode": "gcode"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    index = math.ceil(percentile * len(values)) - 1
    return sorted(values)[max(0, index)]


def _latency(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def _fixtures(root: Path) -> dict[str, Path]:
    def write(
        archive: zipfile.ZipFile, name: str, data: bytes, compression: int
    ) -> None:
        info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
        info.compress_type = compression
        archive.writestr(info, data)

    many = root / "many-small.zip"
    with zipfile.ZipFile(many, "w") as archive:
        for index in range(128):
            write(
                archive,
                f"parts/{index:03}.stl",
                f"solid {index}\n".encode(),
                zipfile.ZIP_DEFLATED,
            )
        for index in range(32):
            write(
                archive,
                f"notes/{index:03}.txt",
                b"ignored",
                zipfile.ZIP_DEFLATED,
            )

    payload = (b"solid benchmark\n" * 524_288)[: 8 * 1024 * 1024]
    stored = root / "large-stored.zip"
    with zipfile.ZipFile(stored, "w") as archive:
        write(archive, "large.stl", payload, zipfile.ZIP_STORED)
    compressed = root / "large-compressed.zip"
    with zipfile.ZipFile(compressed, "w") as archive:
        write(archive, "large.stl", payload, zipfile.ZIP_DEFLATED)
    return {"many_small": many, "large_stored": stored, "large_compressed": compressed}


def _limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_entries=1_000,
        max_entry_bytes=16 * 1024 * 1024,
        max_total_bytes=64 * 1024 * 1024,
        max_central_directory_bytes=4 * 1024 * 1024,
        max_path_bytes=1_024,
        max_depth=32,
    )


def _measure(path: Path, staging: Path, iterations: int) -> tuple[dict, dict]:
    expected_entries = inspect_archive(
        path,
        limits=_limits(),
        file_types=FILE_TYPES,
        image_suffixes=IMAGE_SUFFIXES,
    )
    names = [entry.name for entry in expected_entries if entry.file_type]
    expected_catalog = [
        {
            "entry_id": entry.entry_id,
            "name": entry.name,
            "size_bytes": entry.size_bytes,
            "file_type": entry.file_type,
            "is_image": entry.is_image,
        }
        for entry in expected_entries
    ]
    expected_hashes: dict[str, str] | None = None
    inspect_samples: list[float] = []
    extract_samples: list[float] = []
    bytes_written = 0
    for _ in range(iterations):
        started = time.perf_counter_ns()
        actual = inspect_archive(
            path,
            limits=_limits(),
            file_types=FILE_TYPES,
            image_suffixes=IMAGE_SUFFIXES,
        )
        inspect_samples.append((time.perf_counter_ns() - started) / 1_000_000)
        if [entry.entry_id for entry in actual] != [
            entry.entry_id for entry in expected_entries
        ]:
            raise RuntimeError(
                f"archive inspection changed within one run: {path.name}"
            )

        started = time.perf_counter_ns()
        extracted = extract_selected(
            path,
            names,
            staging_dir=staging,
            max_entry_bytes=_limits().max_entry_bytes,
            importable_suffixes=set(FILE_TYPES),
        )
        extract_samples.append((time.perf_counter_ns() - started) / 1_000_000)
        hashes = {name: _sha256(staged) for staged, name in extracted}
        bytes_written += sum(staged.stat().st_size for staged, _name in extracted)
        for staged, _name in extracted:
            staged.unlink()
        if expected_hashes is None:
            expected_hashes = hashes
        elif hashes != expected_hashes:
            raise RuntimeError(
                f"archive extraction changed within one run: {path.name}"
            )
    return {
        "entries": expected_catalog,
        "extracted_hashes": expected_hashes or {},
    }, {
        "inspection": _latency(inspect_samples),
        "extraction": _latency(extract_samples),
        "bytes_written": bytes_written,
    }


def run(iterations: int) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("Iterations must be positive")
    process = psutil.Process()
    with tempfile.TemporaryDirectory(
        prefix="printstash-archive-benchmark-"
    ) as temporary:
        root = Path(temporary)
        staging = root / "staging"
        staging.mkdir()
        sources = _fixtures(root)
        cpu_started = time.process_time()
        elapsed_started = time.perf_counter()
        correctness: dict[str, dict] = {}
        workloads: dict[str, dict] = {}
        peak_rss = process.memory_info().rss
        for name, path in sources.items():
            correctness[name], workloads[name] = _measure(path, staging, iterations)
            peak_rss = max(peak_rss, process.memory_info().rss)

        oversized = root / "oversized.zip"
        with zipfile.ZipFile(oversized, "w") as archive:
            info = zipfile.ZipInfo("too-large.stl", date_time=ZIP_TIMESTAMP)
            archive.writestr(info, b"x" * 101)
        try:
            inspect_archive(
                oversized,
                limits=ArchiveLimits(10, 100, 100, 1_000, 100, 4),
                file_types=FILE_TYPES,
                image_suffixes=IMAGE_SUFFIXES,
            )
        except ArchivePolicyError as exc:
            rejection = exc.code
        else:
            raise RuntimeError("oversized archive was accepted")

        return {
            "measurement_protocol": "archive-extract-v1",
            "iterations_per_workload": iterations,
            "total_seconds": time.perf_counter() - elapsed_started,
            "process_cpu_seconds": time.process_time() - cpu_started,
            "process_peak_rss_bytes": peak_rss,
            "archive_latency": {
                "p95_ms": max(
                    metric[stage]["p95_ms"]
                    for metric in workloads.values()
                    for stage in ("inspection", "extraction")
                )
            },
            "source_hashes": {name: _sha256(path) for name, path in sources.items()},
            "source_sizes": {
                name: path.stat().st_size for name, path in sources.items()
            },
            "correctness_catalog": correctness,
            "rejection_catalog": {"oversized_entry": rejection},
            "workloads": workloads,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--database", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    report = run(args.iterations)
    report["database_dialect"] = args.database
    if args.compare:
        reference = json.loads(args.compare.read_text())
        for key in (
            "measurement_protocol",
            "source_hashes",
            "correctness_catalog",
            "rejection_catalog",
        ):
            if reference.get(key) != report.get(key):
                raise ValueError(f"archive benchmark correctness differs: {key}")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

"""Collect finite ingestion qualification in a fresh disposable installation.

Actual public ASGI requests, DBOS and native workers are used. Socket latency,
PostgreSQL/S3 and image compatibility are separate qualifications. Short runs
are smoke controls, never substitutes for two hours and 1000 distinct Artifacts.
The parent contains the application process; uncertain cleanup retains its vault.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import chdir, closing, redirect_stdout
from dataclasses import asdict, dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from scripts.benchmark_environment import CgroupLimits


from scripts.benchmark_pipeline_contracts import IngestionObservation, SampleOutcome

BACKEND = Path(__file__).resolve().parents[1]


_MALFORMED_CONTROL_CADENCE = 50


class ArchiveSource(TypedDict):
    name: str
    size: int
    sha256: str


class ResourceSnapshot(TypedDict):
    at_monotonic: float
    rss_current_tree_bytes: int
    fd_current_tree_count: int
    threads_current_tree_count: int
    python_threads_current_count: int
    database_file_bytes: dict[str, int]
    prepared_workspace_file_count: int
    prepared_workspace_bytes: int
    native_temporary_file_count: int
    native_temporary_bytes: int
    process_ids: list[int]
    database_counts: dict[str, int]
    staging_file_count: int
    staging_bytes: int
    temporary_file_count: int
    temporary_bytes: int
    capacity_resources: list[dict[str, object]]
    ledger_ticket_files: dict[str, int]
    scope: str


class Mode(StrEnum):
    SOAK = "soak"
    ARCHIVE = "archive"
    LOAD = "load"


class ObservationPurpose(StrEnum):
    FRESH = "fresh"
    REPLAY = "replay"
    WARMUP = "warmup"


@dataclass(frozen=True)
class QualificationObservation(IngestionObservation):
    purpose: ObservationPurpose = ObservationPurpose.FRESH
    thumbnail_decoded: bool = False


def remaining_job_seconds(
    deadline: float, maximum: float, *, clock: Callable[[], float] = time.monotonic
) -> float:
    """Never admit another input after its finite work window has expired."""
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("qualification_workload_deadline_exhausted")
    return min(maximum, remaining)


def expected_refusal(observation: IngestionObservation) -> bool:
    return observation.outcome == SampleOutcome.REFUSED


def heartbeat_summary(lags: list[float]) -> dict[str, object]:
    ordered = sorted(lags)
    if ordered:
        position = (len(ordered) - 1) * 0.95
        lower = math.floor(position)
        upper = math.ceil(position)
        p95 = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    else:
        p95 = None
    return {
        "samples": len(lags),
        "p95_lag_ms": p95,
        "max_lag_ms": max(lags) if lags else None,
        "under_50ms": p95 is not None and p95 < 50,
        "percentile_method": "linear interpolation over sorted samples",
        "scope": "ASGI application event-loop scheduling lag; excludes socket latency",
    }


def effective_cpu_capacity(
    affinity_count: int | None, limits: CgroupLimits
) -> float | None:
    if affinity_count is None or not limits.cpu_limit_read:
        return None
    return (
        float(affinity_count)
        if limits.cpu_quota_cores is None
        else min(float(affinity_count), limits.cpu_quota_cores)
    )


def throughput_summary(
    one_rate: float | None,
    four_rate: float | None,
    *,
    effective_cpus: float | None,
    native_slots: int,
    native_memory_bytes: int,
    worker_envelope_bytes: int,
) -> dict[str, object]:
    if any(
        not math.isfinite(rate) or rate < 0
        for rate in (one_rate, four_rate)
        if rate is not None
    ):
        raise ValueError("throughput rates must be finite and nonnegative")
    assessed = one_rate is not None and four_rate is not None
    ratio = (
        four_rate / one_rate
        if one_rate is not None and one_rate > 0 and four_rate is not None
        else None
    )
    budget_workers = min(native_slots, native_memory_bytes // worker_envelope_bytes)
    eligible = (
        assessed
        and effective_cpus is not None
        and effective_cpus >= 4
        and budget_workers >= 4
    )
    if not assessed:
        reason = "missing_load_baseline_cells"
    elif effective_cpus is None:
        reason = "effective_cpu_capacity_unknown"
    elif effective_cpus < 4:
        reason = "fewer_than_four_effective_cpus"
    elif budget_workers < 4:
        reason = "physical_native_budget_below_four_workers"
    elif ratio is None:
        reason = "serial_useful_throughput_missing"
    elif ratio < 2.5:
        reason = "four_to_one_ratio_below_2_5"
    else:
        reason = "qualified_full_ingestion_ratio"
    passed = ratio is not None and ratio >= 2.5 if eligible else None
    return {
        "four_to_one_ratio": ratio,
        "minimum_ratio": 2.5,
        "effective_cpus": effective_cpus,
        "budget_workers": budget_workers,
        "worker_envelope_bytes": worker_envelope_bytes,
        "assessed": assessed,
        "eligible": eligible,
        "gate_passed": passed,
        "qualification": "not_assessed_missing_baseline_cells"
        if not assessed
        else "not_qualified_N/A"
        if not eligible
        else "passed"
        if passed
        else "failed",
        "reason": reason,
        "scope": "actual import through original download, terminal derivatives and thumbnail decode; includes qualification checks",
    }


def load_admission_environment(concurrencies: list[int]) -> dict[str, str]:
    """Match private throughput admission to its largest requested cell.

    Only the logical per-user lease count changes. Byte, global, disk headroom
    and native budgets retain the private installation's ordinary settings.
    """
    return {"VAULT_STAGING_MAX_ACTIVE_PER_USER": str(max(concurrencies))}


async def heartbeat(stop: threading.Event, lags: list[float]) -> None:
    while not stop.is_set():
        target = time.perf_counter() + 0.01
        await asyncio.sleep(0.01)
        lags.append(max(0.0, (time.perf_counter() - target) * 1000))


def journal(path: Path, value: dict[str, object]) -> None:
    with path.open("a") as stream:
        stream.write(json.dumps(value, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic_report(path: Path, report: dict[str, object]) -> None:
    temporary = path.with_suffix(".pending")
    try:
        with temporary.open("w") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def build_soak_corpus(root: Path) -> tuple[Path, ...]:
    """Closed disjoint cubes vary useful geometry, not headers or padding."""
    import struct

    cube = (root / "cube-binary.stl").read_bytes()
    facet = struct.Struct("<12fH")
    records = list(facet.iter_unpack(cube[84:]))
    paths = [root / "cube-binary.stl", root / "cube-mm.3mf"]
    expected: list[dict[str, object]] = [
        {
            "name": path.name,
            "faces": 12,
            "volume_mm3": 8000,
            "bbox_mm": [20, 20, 20],
            "input_bytes": path.stat().st_size,
        }
        for path in paths
    ]
    for label, cubes in (("medium", 417), ("large", 1667)):
        payload = bytearray(
            f"qualification closed cubes {label}".encode().ljust(80, b"\0")
        )
        payload.extend(struct.pack("<I", cubes * 12))
        for ordinal in range(cubes):
            shift = (30 * (ordinal % 42), 30 * (ordinal // 42), 0)
            for record in records:
                values = list(record)
                for vertex in range(3):
                    for axis in range(3):
                        values[3 + vertex * 3 + axis] += shift[axis]
                payload.extend(facet.pack(*values))
        path = root / f"closed-cubes-{label}.stl"
        path.write_bytes(payload)
        paths.append(path)
        expected.append(
            {
                "name": path.name,
                "faces": cubes * 12,
                "closed_components": cubes,
                "volume_mm3": cubes * 8000,
                "bbox_mm": [
                    30 * (min(cubes, 42) - 1) + 20,
                    30 * ((cubes - 1) // 42) + 20,
                    20,
                ],
                "input_bytes": len(payload),
            }
        )
    atomic_report(root / "soak-manifest.json", {"fixtures": expected})
    return tuple(paths)


def variant(source: Path, destination: Path, ordinal: int) -> None:
    """Change only container metadata; preserve the canonical geometry payload."""
    if ordinal < 0 or destination.exists():
        raise ValueError("invalid_variant_destination")
    shutil.copyfile(source, destination)
    stamp = f"qualification-source-{ordinal:016d}".encode("ascii")
    if source.suffix.lower() == ".stl":
        size = source.stat().st_size
        with destination.open("r+b") as stream:
            stream.seek(80)
            count_bytes = stream.read(4)
            if (
                len(count_bytes) != 4
                or size != 84 + int.from_bytes(count_bytes, "little") * 50
            ):
                raise ValueError("qualification_requires_binary_stl")
            stream.seek(0)
            stream.write(stamp.ljust(80, b"\0"))
    elif source.suffix.lower() == ".3mf":
        with zipfile.ZipFile(destination, "a") as archive:
            archive.comment = stamp
    else:
        raise ValueError("unsupported_qualification_source")


def summarize(
    samples: list[QualificationObservation], *, elapsed_seconds: float
) -> dict[str, object]:
    from scripts.benchmark_pipeline_contracts import SampleOutcome

    usable_samples = [
        sample
        for sample in samples
        if sample.purpose == ObservationPurpose.FRESH
        and sample.outcome == SampleOutcome.COMPLETED
        and sample.original_verified is True
        and sample.file_id is not None
        and sample.artifact_reused is False
        and sample.source_preexisting is False
        and sample.thumbnail_decoded is True
    ]
    usable = min(
        len({sample.file_id for sample in usable_samples}),
        len({sample.input_sha256 for sample in usable_samples}),
    )
    return {
        "observations": len(samples),
        "purposes": dict(Counter(sample.purpose.value for sample in samples)),
        "distinct_usable_artifacts": usable,
        "outcomes": dict(Counter(sample.outcome.value for sample in samples)),
        "elapsed_seconds": elapsed_seconds,
        "soak_thresholds_met": elapsed_seconds >= 7200 and usable >= 1000,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", type=Mode, choices=list(Mode), default=Mode.SOAK)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--duration-seconds", type=float, default=7200)
    parser.add_argument("--min-artifacts", type=int, default=1000)
    parser.add_argument("--deadline-seconds", type=float, default=9000)
    parser.add_argument("--job-deadline-seconds", type=float, default=180)
    parser.add_argument("--drain-seconds", type=float, default=300)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument(
        "--native-slots",
        type=int,
        help="private LOAD native slot ceiling, CPU and physical budget constrained",
    )
    parser.add_argument(
        "--load-concurrency",
        type=int,
        choices=(1, 2, 4, 8),
        nargs="+",
        help="LOAD cells to measure, in requested order (default: 1 2 4 8)",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not all(
        math.isfinite(value)
        for value in (
            args.duration_seconds,
            args.deadline_seconds,
            args.job_deadline_seconds,
            args.drain_seconds,
        )
    ):
        parser.error("bounds must be finite")
    if (
        not 0 <= args.duration_seconds
        or not 0 < args.deadline_seconds <= 86400
        or (args.mode == Mode.SOAK and args.duration_seconds >= args.deadline_seconds)
    ):
        parser.error("duration must be nonnegative and below finite deadline <=86400")
    if args.job_deadline_seconds <= 0 or args.drain_seconds <= 0:
        parser.error("job and drain deadlines must be positive")
    if not 1 <= args.min_artifacts <= 10000 or not 1 <= args.samples <= 10000:
        parser.error("counts must be between 1 and 10000")
    if args.native_slots is not None and (
        args.mode != Mode.LOAD or not 1 <= args.native_slots <= 8
    ):
        parser.error("native-slots requires load mode and must be between 1 and 8")
    if args.load_concurrency is not None:
        if args.mode != Mode.LOAD:
            parser.error("load-concurrency requires load mode")
        if len(set(args.load_concurrency)) != len(args.load_concurrency):
            parser.error("load-concurrency must not contain duplicates")
    elif args.mode == Mode.LOAD:
        args.load_concurrency = [1, 2, 4, 8]
    if args.mode == Mode.ARCHIVE and (
        args.archive is None or not args.archive.is_file()
    ):
        parser.error("archive mode requires an existing ZIP")
    if args.archive is not None and args.mode != Mode.ARCHIVE:
        parser.error("archive is valid only in archive mode")
    args.output_dir = args.output_dir.resolve()
    if args.archive is not None:
        args.archive = args.archive.resolve()
    if not args.worker and args.output_dir.exists():
        parser.error("output directory must not exist")
    return args


def setup_client(client: TestClient, data_dir: Path, thumb_dir: Path) -> str:
    password = secrets.token_urlsafe(24)
    client.headers["Origin"] = "http://testserver"
    csrf = client.post("/api/v1/setup/session")
    csrf.raise_for_status()
    client.headers["X-PrintStash-Setup-CSRF"] = csrf.json()["csrf"]
    response = client.post(
        "/api/v1/setup",
        json={
            "username": "qualification-owner",
            "password": password,
            "storage_backend": "local",
            "data_dir": str(data_dir),
            "thumb_dir": str(thumb_dir),
        },
    )
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return password


def refresh_client(client: TestClient, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "qualification-owner", "password": password},
    )
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]


def resource_snapshot(root: Path, db: Path) -> ResourceSnapshot:
    """Current sampled values, not cumulative high-water or leak attribution."""
    import psutil

    current = psutil.Process()
    processes = [current, *current.children(recursive=True)]
    rss = 0
    descriptors = 0
    threads = 0
    process_ids: list[int] = []
    for process in processes:
        try:
            rss += process.memory_info().rss
            descriptors += process.num_fds()
            threads += process.num_threads()
            process_ids.append(process.pid)
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
    counts: dict[str, int] = {}
    with closing(
        sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
    ) as connection:
        for name in (
            "files",
            "capacity_reservations",
            "staging_leases",
            "mesh_fingerprint_continuations",
            "jobs",
            "artifact_derivatives",
        ):
            counts[name] = connection.execute(
                f"SELECT COUNT(*) FROM {name}"
            ).fetchone()[0]
        capacity_resources = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT resources_json FROM capacity_reservations"
            )
        ]
        counts["active_jobs"] = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','interrupted')"
        ).fetchone()[0]
    staging_bytes = 0
    staging_count = 0
    for path in (root / "staging").rglob("*"):
        try:
            if path.is_file():
                staging_bytes += path.stat().st_size
                staging_count += 1
        except FileNotFoundError:
            continue  # Sampling is not an atomic filesystem inventory.

    def files_under(paths: list[Path]) -> tuple[int, int]:
        size = count = 0
        for path in paths:
            descendants = path.rglob("*") if path.is_dir() else [path]
            for entry in descendants:
                try:
                    if entry.is_file():
                        size += entry.stat().st_size
                        count += 1
                except FileNotFoundError:
                    continue  # Native cleanup may commit during this observation.
        return count, size

    prepared_root = root / "runtime" / "prepared" / "sources"
    prepared_count, prepared_bytes = files_under([prepared_root])
    outer_temporary = [
        child
        for child in root.parent.iterdir()
        if child.name not in {root.name, "inputs"}
    ]
    outer_count, outer_bytes = files_under(outer_temporary)
    native_directories = [
        *root.parent.glob("printstash-mesh-*"),
        *prepared_root.glob("*/printstash-mesh-*"),
    ]
    native_count, native_bytes = files_under(native_directories)
    from app.runtime.engine.dbos_engine import system_database_url

    dbos_url, _schema = system_database_url(f"sqlite:///{db}")
    dbos_path = Path(dbos_url.removeprefix("sqlite:///"))
    database_file_bytes = {}
    for base in (db, dbos_path):
        for path in (base, Path(str(base) + "-wal"), Path(str(base) + "-shm")):
            try:
                database_file_bytes[path.name] = path.stat().st_size
            except FileNotFoundError:
                database_file_bytes[path.name] = 0  # No journal currently retained.
    return {
        "at_monotonic": time.monotonic(),
        "rss_current_tree_bytes": rss,
        "fd_current_tree_count": descriptors,
        "threads_current_tree_count": threads,
        "python_threads_current_count": threading.active_count(),
        "database_file_bytes": database_file_bytes,
        "prepared_workspace_file_count": prepared_count,
        "prepared_workspace_bytes": prepared_bytes,
        "native_temporary_file_count": native_count,
        "native_temporary_bytes": native_bytes,
        "process_ids": process_ids,
        "database_counts": counts,
        "staging_file_count": staging_count,
        "staging_bytes": staging_bytes,
        "temporary_file_count": outer_count + prepared_count,
        "temporary_bytes": outer_bytes + prepared_bytes,
        "capacity_resources": capacity_resources,
        "ledger_ticket_files": {
            name: len(tuple((root / "runtime" / name).rglob("*.ticket")))
            for name in ("native", "inference-models", "prepared", "source-io")
        },
        "scope": "sampled_current_process_tree_threads_FDs; temporary files include prepared/native workspaces; SQL/DBOS history and journals are retained data, not temporary files; ledger files may include released receipts",
    }


def wait_job(client: TestClient, job_id: str, deadline: float):
    from app.schemas.jobs import JobStatus

    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        status = JobStatus.model_validate(response.json())
        if status.terminal:
            return status
        time.sleep(0.05)
    raise TimeoutError(f"job_deadline:{job_id}")


def archive_run(
    client: TestClient, source: Path, deadline: float, output: Path
) -> dict[str, object]:
    """Use reviewed public selection, then verify every original through HTTP."""
    from sqlmodel import select

    from app.db.models import File
    from app.db.session import get_session_factory

    entries: list[ArchiveSource] = []
    with source.open("rb") as stream:
        archive_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            if not entry.is_dir() and Path(entry.filename).suffix.lower() in {
                ".stl",
                ".3mf",
            }:
                with archive.open(entry) as stream:
                    hasher = hashlib.sha256()
                    while block := stream.read(1024 * 1024):
                        hasher.update(block)
                    digest = hasher.hexdigest()
                entries.append(
                    {"name": entry.filename, "size": entry.file_size, "sha256": digest}
                )
    atomic_report(
        output / "archive-input.json",
        {"archive_sha256": archive_sha, "entries": entries},
    )
    if not entries:
        raise ValueError("no_model_entries")
    with source.open("rb") as stream:
        inspected = client.post(
            "/api/v1/ingest/archive/inspect",
            files={"file": (source.name, stream, "application/zip")},
        )
    inspected.raise_for_status()
    review = wait_job(client, inspected.json()["job_id"], deadline)
    if review.result is None or review.result.get("kind") != "archive_manifest":
        raise RuntimeError(f"archive_inspection_failed:{review.error}")
    response = client.post(
        f"/api/v1/ingest/archive/{review.job_id}/select",
        json={"names": [entry["name"] for entry in entries]},
    )
    response.raise_for_status()
    batch = wait_job(client, response.json()["job_id"], deadline)
    with get_session_factory().scoped_session() as session:
        artifacts = {file.sha256: file.id for file in session.exec(select(File)).all()}
    verified: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        result = verify_archive_artifact(
            client, entry, artifacts.get(entry["sha256"]), deadline
        )
        result["duplicate_source"] = entry["sha256"] in seen
        seen.add(entry["sha256"])
        verified.append(result)
        journal(output / "archive-entries.jsonl", result)
    with source.open("rb") as stream:
        unchanged = hashlib.file_digest(stream, "sha256").hexdigest() == archive_sha
    return {
        "verification_complete": archive_verified(
            batch.state.value, unchanged, verified
        ),
        "archive_sha256": archive_sha,
        "source_unchanged": unchanged,
        "logical_entries": len(entries),
        "distinct_source_hashes": len({entry["sha256"] for entry in entries}),
        "job": batch.model_dump(mode="json"),
        "entries": verified,
    }


def archive_verified(
    batch_state: str, source_unchanged: bool, entries: list[dict[str, object]]
) -> bool:
    return (
        batch_state == "completed"
        and source_unchanged
        and bool(entries)
        and all(
            entry["original_verified"] is True
            and entry["outcome"] in {"completed", "refused"}
            for entry in entries
        )
    )


def verify_archive_artifact(
    client: TestClient,
    entry: ArchiveSource,
    file_id: int | None,
    deadline: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    from PIL import Image
    from pydantic import TypeAdapter

    from app.db.models import DerivativeKind
    from app.schemas.jobs import DerivativeRead, DerivativeStatus
    from scripts.benchmark_pipeline_contracts import unavailable_derivative_outcome

    result: dict[str, object] = {
        **entry,
        "file_id": file_id,
        "original_verified": False,
        "thumbnail_decoded": False,
        "derivatives": [],
        "outcome": "failed",
    }
    if file_id is None:
        result["error"] = "source_artifact_missing"
        return result
    expected = {DerivativeKind.METADATA, DerivativeKind.THUMBNAIL}
    terminal = {
        DerivativeStatus.READY,
        DerivativeStatus.FAILED,
        DerivativeStatus.SKIPPED,
        DerivativeStatus.CANCELLED,
        DerivativeStatus.DISABLED,
    }
    adapter = TypeAdapter(list[DerivativeRead])
    try:
        original = client.get(f"/api/v1/files/{file_id}/download")
        original.raise_for_status()
        result["original_verified"] = (
            hashlib.sha256(original.content).hexdigest() == entry["sha256"]
        )
        if not result["original_verified"]:
            raise ValueError("original_hash_mismatch")
        while clock() < deadline:
            response = client.get(f"/api/v1/files/{file_id}/derivatives")
            response.raise_for_status()
            derivatives = [
                row
                for row in adapter.validate_python(response.json())
                if row.kind in expected
            ]
            result["derivatives"] = [row.model_dump(mode="json") for row in derivatives]
            if {row.kind for row in derivatives} == expected and all(
                row.state in terminal for row in derivatives
            ):
                break
            pause(0.1)
        else:
            result.update(outcome="timeout", error="expected_derivatives_deadline")
            return result
        failures = [row for row in derivatives if row.state != DerivativeStatus.READY]
        if failures:
            result["outcome"] = unavailable_derivative_outcome(failures).value
            result["error"] = ";".join(
                f"{row.kind.value}:{row.failure_reason if row.failure_reason is not None else row.state.value}"
                for row in failures
            )
            return result
        preview = client.get(f"/api/v1/files/{file_id}/thumbnail")
        result["thumbnail_http_status"] = preview.status_code
        if preview.status_code != 200:
            raise ValueError(f"thumbnail_http_{preview.status_code}")
        result["thumbnail_sha256"] = hashlib.sha256(preview.content).hexdigest()
        with Image.open(BytesIO(preview.content)) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise ValueError("thumbnail_has_no_pixels")
            result["thumbnail_format"] = image.format
            result["thumbnail_dimensions"] = list(image.size)
        result["thumbnail_decoded"] = True
        result["outcome"] = "completed"
    except Exception as exc:  # noqa: BLE001 - preserve each source failure
        result.update(outcome="failed", error=f"{type(exc).__name__}: {exc}")
    return result


def qualify_observation(
    client: TestClient,
    observation: IngestionObservation,
    purpose: ObservationPurpose,
    deadline: float,
) -> QualificationObservation:
    values = asdict(observation)
    decoded = False
    if observation.outcome == SampleOutcome.COMPLETED:
        checked = verify_archive_artifact(
            client,
            {
                "name": observation.name,
                "size": observation.input_bytes,
                "sha256": observation.input_sha256,
            },
            observation.file_id,
            deadline,
        )
        values["outcome"] = SampleOutcome(str(checked["outcome"]))
        values["original_verified"] = checked["original_verified"]
        decoded = checked["thumbnail_decoded"] is True
        if values["outcome"] != SampleOutcome.COMPLETED:
            values["reason"] = str(checked["error"])
    return QualificationObservation(
        **values, purpose=purpose, thumbnail_decoded=decoded
    )


def malformed_control(
    client: TestClient, scratch: Path, deadline: float, ordinal: int
) -> dict[str, object]:
    from scripts.benchmark_ingestion import measure_ingestion
    from scripts.benchmark_pipeline_contracts import InputIdentity

    malformed = scratch / f"malformed-control-{ordinal}.3mf"
    original = f"qualification malformed 3MF control {ordinal}".encode()
    malformed.write_bytes(original)
    try:
        refusal = measure_ingestion(
            client,
            malformed,
            InputIdentity(
                malformed.name,
                hashlib.sha256(original).hexdigest(),
                len(original),
                ordinal,
            ),
            deadline_seconds=max(0.01, deadline - time.monotonic()),
        )
        return {
            "malformed": asdict(refusal),
            "expected_refusal_observed": expected_refusal(refusal),
            "malformed_source_unchanged": malformed.read_bytes() == original,
            "purpose": "malformed_control_excluded_from_artifact_count",
        }
    finally:
        malformed.unlink(missing_ok=True)


def control_run(
    client: TestClient, scratch: Path, deadline: float
) -> dict[str, object]:
    """Keep real malformed/cancel/retry observations separate from soak credits."""
    from app.db.models import JobState

    malformed_result = malformed_control(client, scratch, deadline, -1)
    source = scratch / "cancel-control.stl"
    variant(scratch / "corpus" / "cube-binary.stl", source, 1000000)
    with source.open("rb") as stream:
        accepted = client.post(
            "/api/v1/ingest/model",
            files={"file": (source.name, stream, "application/octet-stream")},
        )
    accepted.raise_for_status()
    job_id = accepted.json()["job_id"]
    response = client.post(f"/api/v1/jobs/{job_id}/cancel")
    cancellation: dict[str, object] = {"http_status": response.status_code}
    cancellation_valid = False
    if response.status_code == 200:
        cancelled = wait_job(client, job_id, deadline)
        cancellation["job"] = cancelled.model_dump(mode="json")
        if cancelled.state == JobState.CANCELLED:
            retried = client.post(f"/api/v1/jobs/{job_id}/retry")
            retried.raise_for_status()
            retry = wait_job(client, job_id, deadline)
            cancellation["retry"] = retry.model_dump(mode="json")
            cancellation_valid = retry.state == JobState.COMPLETED
    elif response.status_code == 409:
        raced = wait_job(client, job_id, deadline)
        cancellation["job"] = raced.model_dump(mode="json")
        cancellation_valid = raced.state == JobState.COMPLETED
        cancellation["qualification"] = (
            "completed_before_cancel; cancellation not proved by this control"
        )
    else:
        response.raise_for_status()
    return {
        **malformed_result,
        "cancellation": cancellation,
        "cancellation_valid": cancellation_valid,
    }


def run_worker(args: argparse.Namespace) -> int:
    # Include environment discovery and cold imports in the finite worker
    # budget; the parent independently contains the whole child invocation.
    started = time.monotonic()
    from scripts.bench_mesh_pipeline import (
        configure_private_vault,
        export_private_settings,
    )
    from scripts.benchmark_pipeline_contracts import InputIdentity
    from scripts.mesh_benchmark_corpus import build_contract_corpus

    output = args.output_dir
    workspace = output / "workspace"
    workspace.mkdir()
    vault = workspace / "vault"
    scratch = workspace / "inputs"
    scratch.mkdir()
    configure_private_vault(vault)
    from scripts.benchmark_environment import collect_environment

    environment = collect_environment()
    effective_cpus = effective_cpu_capacity(
        environment.affinity_cpu_count, environment.cgroup
    )
    if args.mode == Mode.LOAD:
        os.environ.update(load_admission_environment(args.load_concurrency))
        requested_slots = args.native_slots if args.native_slots is not None else 4
        cpu_slots = (
            max(1, math.floor(effective_cpus)) if effective_cpus is not None else 1
        )
        private_slots = min(requested_slots, cpu_slots)
        os.environ["VAULT_MAX_RENDER_JOBS"] = str(private_slots)
        os.environ["VAULT_JOBS_DERIVE_NATIVE_CONCURRENCY"] = str(private_slots)
    os.environ["TMPDIR"] = str(workspace)
    tempfile.tempdir = str(workspace)
    report: dict[str, object] = {
        "schema_version": 1,
        "mode": args.mode.value,
        "scope": "private_actual_asgi_sqlite_local_dbos; excludes socket latency and external backends",
        "decision": "pending",
        "summary": summarize([], elapsed_seconds=0),
        "workspace": str(workspace),
        "errors": [],
        "requested": {
            key: value
            for key, value in vars(args).items()
            if key not in {"output_dir", "worker", "archive"}
        },
        "resource_review_required": True,
    }
    errors: list[str] = []
    samples: list[QualificationObservation] = []
    stop = threading.Event()
    sample_errors: list[str] = []
    sampler: threading.Thread | None = None
    cleanup = None
    retain = True
    soak_seconds = 0.0
    drain_reserve_seconds = min(args.drain_seconds + 15, args.deadline_seconds / 3)
    workload_deadline = started + args.deadline_seconds - drain_reserve_seconds
    atomic_report(output / "report.json", report)
    try:
        with chdir(workspace), redirect_stdout(sys.stderr):
            from app.core.config import settings

            export_private_settings(settings)
            from fastapi.testclient import TestClient

            from app.db.migrate import main as migrate
            from app.main import app
            from app.modules.storage.storage_backend import generations
            from app.runtime import maintenance
            from scripts.benchmark_cleanup import cleanup_private_jobs
            from scripts.benchmark_ingestion import measure_ingestion, observe_teardown

            report["environment"] = asdict(environment)
            from app.modules.media.native_budget import MIN_ANALYSIS_MEMORY
            from app.modules.media.native_process import native_capacity

            capacity = native_capacity()
            report["physical_budget"] = {
                "native_concurrency": os.environ[
                    "VAULT_JOBS_DERIVE_NATIVE_CONCURRENCY"
                ],
                "geometry_memory_fraction": settings.mesh_memory_budget_fraction,
                "native_slots": capacity.slots,
                "native_memory_bytes": capacity.bytes,
                "worker_envelope_bytes": MIN_ANALYSIS_MEMORY,
                "effective_cpus": effective_cpus,
                "affinity_cpu_count": environment.affinity_cpu_count,
                "cgroup_cpu_quota_cores": environment.cgroup.cpu_quota_cores,
                "cgroup_cpu_limit_read": environment.cgroup.cpu_limit_read,
                "qualified_parallel_workers": min(
                    capacity.slots, capacity.bytes // MIN_ANALYSIS_MEMORY
                ),
            }
            if args.mode == Mode.LOAD:
                report["load_admission_profile"] = {
                    "requested_concurrency": args.load_concurrency,
                    "staging_max_active_per_user": settings.staging_max_active_per_user,
                    "staging_max_pending": settings.staging_max_pending,
                    "staging_max_bytes": settings.staging_max_gb * 1024**3,
                    "staging_min_free_bytes": settings.staging_min_free_gb * 1024**3,
                    "scope": "private throughput admission; only per-user lease count follows selected cells; global, byte, free-space and native budgets unchanged",
                }
            migrate()
            epoch = generations.current_epoch()
            with TestClient(app) as client:
                password = setup_client(client, settings.data_dir, settings.thumb_dir)
                db = Path(settings.db_url.removeprefix("sqlite:///"))
                ready_at = time.monotonic()
                report["timing"] = {
                    "startup_seconds": ready_at - started,
                    "workload_seconds_available_after_startup": max(
                        0.0, workload_deadline - ready_at
                    ),
                    "drain_reserve_seconds": drain_reserve_seconds,
                    "total_deadline_seconds": args.deadline_seconds,
                    "scope": "startup consumes the fixed total budget; workload duration and throughput exclude startup",
                }
                atomic_report(output / "report.json", report)

                build_contract_corpus(scratch / "corpus")
                warm_path = scratch / "warmup.stl"
                variant(scratch / "corpus" / "cube-binary.stl", warm_path, 2000000)
                warm_bytes = warm_path.read_bytes()
                warmed = measure_ingestion(
                    client,
                    warm_path,
                    InputIdentity(
                        warm_path.name,
                        hashlib.sha256(warm_bytes).hexdigest(),
                        len(warm_bytes),
                        -2,
                    ),
                    deadline_seconds=remaining_job_seconds(
                        workload_deadline, args.job_deadline_seconds
                    ),
                )
                warmup = qualify_observation(
                    client, warmed, ObservationPurpose.WARMUP, workload_deadline
                )
                samples.append(warmup)
                journal(output / "observations.jsonl", asdict(warmup))
                report["warmup"] = asdict(warmup)
                if warmup.outcome != SampleOutcome.COMPLETED:
                    errors.append("native_warmup_failed")
                warm_path.unlink()
                report["warm_resources"] = resource_snapshot(vault, db)
                atomic_report(output / "report.json", report)

                def observe() -> None:
                    while not stop.is_set():
                        try:
                            value = resource_snapshot(vault, db)
                            journal(output / "resources.jsonl", dict(value))
                        except Exception as exc:  # noqa: BLE001 - preserve sampling failure
                            sample_errors.append(f"{type(exc).__name__}: {exc}")
                        stop.wait(5)

                sampler = threading.Thread(
                    target=observe, name="qualification-sampler", daemon=True
                )
                sampler.start()
                try:
                    if args.mode == Mode.ARCHIVE:
                        archive_report = archive_run(
                            client, args.archive, workload_deadline, output
                        )
                        report["archive"] = archive_report
                        if not archive_report["verification_complete"]:
                            errors.append("archive_verification_incomplete")
                    else:
                        sources = (
                            scratch / "corpus" / "cube-binary.stl",
                            scratch / "corpus" / "cube-mm.3mf",
                        )
                        if args.mode == Mode.SOAK:
                            sources = build_soak_corpus(scratch / "corpus")
                            report["soak_corpus"] = json.loads(
                                (scratch / "corpus" / "soak-manifest.json").read_text()
                            )
                            report["malformed_control_every_artifacts"] = (
                                _MALFORMED_CONTROL_CADENCE
                            )
                        if args.mode == Mode.SOAK and args.duration_seconds >= 7200:
                            controls = control_run(
                                client,
                                scratch,
                                min(
                                    workload_deadline,
                                    time.monotonic() + args.job_deadline_seconds,
                                ),
                            )
                            report["controls"] = controls
                            if (
                                not controls["expected_refusal_observed"]
                                or not controls["malformed_source_unchanged"]
                            ):
                                errors.append("malformed_control_not_proved")
                            if not controls["cancellation_valid"]:
                                errors.append("cancellation_control_failed")
                            atomic_report(output / "report.json", report)
                        ordinal = 0
                        last_login = time.monotonic()
                        workload_started = time.monotonic()

                        def submit(index: int, replay: bool = False):
                            source = sources[index % len(sources)]
                            path = scratch / f"input-{index}{source.suffix}"
                            if not path.exists():
                                variant(source, path, index)
                            with path.open("rb") as stream:
                                digest = hashlib.file_digest(
                                    stream, "sha256"
                                ).hexdigest()
                            identity = InputIdentity(
                                path.name, digest, path.stat().st_size, index
                            )
                            observed = measure_ingestion(
                                client,
                                path,
                                identity,
                                deadline_seconds=remaining_job_seconds(
                                    workload_deadline, args.job_deadline_seconds
                                ),
                            )
                            tagged = qualify_observation(
                                client,
                                observed,
                                ObservationPurpose.REPLAY
                                if replay
                                else ObservationPurpose.FRESH,
                                workload_deadline,
                            )
                            journal(output / "observations.jsonl", asdict(tagged))
                            if observed.original_verified is True:
                                path.unlink()
                            return tagged

                        if args.mode == Mode.LOAD:
                            cells = []
                            for concurrency in args.load_concurrency:
                                cell_started = time.monotonic()
                                heartbeat_stop = threading.Event()
                                lags: list[float] = []
                                if client.portal is None:
                                    raise RuntimeError("lifespan_portal_missing")
                                heartbeat_future = client.portal.start_task_soon(
                                    heartbeat, heartbeat_stop, lags
                                )
                                try:
                                    with ThreadPoolExecutor(
                                        max_workers=concurrency
                                    ) as pool:
                                        current = list(
                                            pool.map(
                                                submit,
                                                range(ordinal, ordinal + args.samples),
                                            )
                                        )
                                finally:
                                    heartbeat_stop.set()
                                    heartbeat_future.result(timeout=5)
                                cell_elapsed = time.monotonic() - cell_started
                                loop_observation = heartbeat_summary(lags)
                                if not loop_observation["under_50ms"]:
                                    errors.append(f"load_heartbeat_gate:{concurrency}")
                                if summarize(current, elapsed_seconds=cell_elapsed)[
                                    "distinct_usable_artifacts"
                                ] != len(current):
                                    errors.append(
                                        f"load_incomplete_samples:{concurrency}"
                                    )
                                journal(
                                    output / "heartbeats.jsonl",
                                    {"concurrency": concurrency, "lags_ms": lags},
                                )
                                ordinal += args.samples
                                samples.extend(current)
                                cells.append(
                                    {
                                        "concurrency": concurrency,
                                        "elapsed_seconds": cell_elapsed,
                                        "usable_artifacts_per_second": sum(
                                            item.outcome == SampleOutcome.COMPLETED
                                            for item in current
                                        )
                                        / cell_elapsed,
                                        "heartbeat": loop_observation,
                                        "samples": [asdict(item) for item in current],
                                    }
                                )
                                atomic_report(
                                    output / "report.json",
                                    {**report, "load_cells": cells},
                                )
                            report["load_cells"] = cells
                            rates = {
                                cell["concurrency"]: cell["usable_artifacts_per_second"]
                                for cell in cells
                            }
                            scaling = throughput_summary(
                                rates.get(1),
                                rates.get(4),
                                effective_cpus=effective_cpus,
                                native_slots=capacity.slots,
                                native_memory_bytes=capacity.bytes,
                                worker_envelope_bytes=MIN_ANALYSIS_MEMORY,
                            )
                            report["throughput_gate"] = scaling
                            if (
                                scaling["eligible"]
                                and scaling["gate_passed"] is not True
                            ):
                                errors.append("load_four_to_one_throughput_gate")
                        else:
                            while time.monotonic() < workload_deadline:
                                elapsed = time.monotonic() - workload_started
                                count = summarize(samples, elapsed_seconds=elapsed)[
                                    "distinct_usable_artifacts"
                                ]
                                if (
                                    elapsed >= args.duration_seconds
                                    and count >= args.min_artifacts
                                ):
                                    break
                                if time.monotonic() - last_login >= 1800:
                                    refresh_client(client, password)
                                    last_login = time.monotonic()
                                fresh = submit(ordinal)
                                samples.append(fresh)
                                if fresh.outcome in {
                                    SampleOutcome.FAILED,
                                    SampleOutcome.TIMEOUT,
                                }:
                                    errors.append(
                                        f"soak_sample_{ordinal}:{fresh.outcome.value}:{fresh.reason}"
                                    )
                                if (
                                    ordinal > 0
                                    and ordinal % _MALFORMED_CONTROL_CADENCE == 0
                                ):
                                    periodic_control = malformed_control(
                                        client,
                                        scratch,
                                        min(
                                            workload_deadline,
                                            time.monotonic()
                                            + args.job_deadline_seconds,
                                        ),
                                        ordinal,
                                    )
                                    journal(
                                        output / "malformed-controls.jsonl",
                                        periodic_control,
                                    )
                                    if (
                                        not periodic_control[
                                            "expected_refusal_observed"
                                        ]
                                        or not periodic_control[
                                            "malformed_source_unchanged"
                                        ]
                                    ):
                                        errors.append(
                                            f"periodic_malformed_control_not_proved:{ordinal}"
                                        )
                                    replay = submit(ordinal, replay=True)
                                    samples.append(replay)
                                    if (
                                        replay.artifact_reused is not True
                                        or replay.outcome != SampleOutcome.COMPLETED
                                    ):
                                        errors.append(
                                            f"replay_integrity_failed:{ordinal}"
                                        )
                                ordinal += 1
                                report["summary"] = summarize(
                                    samples,
                                    elapsed_seconds=time.monotonic() - workload_started,
                                )
                                atomic_report(output / "report.json", report)
                                target = (
                                    workload_started
                                    + ordinal
                                    * args.duration_seconds
                                    / args.min_artifacts
                                )
                                while time.monotonic() < min(target, workload_deadline):
                                    stop.wait(
                                        min(
                                            1,
                                            min(target, workload_deadline)
                                            - time.monotonic(),
                                        )
                                    )
                            soak_seconds = time.monotonic() - workload_started
                            report["summary"] = summarize(
                                samples, elapsed_seconds=soak_seconds
                            )
                        if args.mode == Mode.LOAD:
                            report["summary"] = summarize(
                                samples,
                                elapsed_seconds=time.monotonic() - workload_started,
                            )
                except BaseException as exc:  # noqa: BLE001 - keep lifespan exit normal after cancellation
                    errors.append(f"{type(exc).__name__}: {exc}")
                finally:
                    drain_started = time.monotonic()
                    drain_deadline = min(
                        drain_started + args.drain_seconds,
                        started + args.deadline_seconds - 15,
                    )
                    while True:
                        drained = resource_snapshot(vault, db)
                        journal(output / "drain-resources.jsonl", dict(drained))
                        if all(
                            drained["database_counts"][name] == 0
                            for name in (
                                "active_jobs",
                                "capacity_reservations",
                                "staging_leases",
                                "mesh_fingerprint_continuations",
                            )
                        ):
                            report["natural_drain_complete"] = True
                            break
                        if time.monotonic() >= drain_deadline:
                            report["natural_drain_complete"] = False
                            errors.append("natural_drain_deadline")
                            break
                        stop.wait(min(5, max(0, drain_deadline - time.monotonic())))
                    report["natural_drain_seconds"] = time.monotonic() - drain_started
                    stop.set()
                    sampler.join(timeout=5)
                    if sampler.is_alive():
                        errors.append("resource_sampler_did_not_stop")
                    cleanup = cleanup_private_jobs(
                        client,
                        deadline_seconds=max(
                            0,
                            min(
                                args.drain_seconds,
                                started + args.deadline_seconds - time.monotonic() - 10,
                            ),
                        ),
                    )
                    final_snapshot = resource_snapshot(vault, db)
                    report["final_resources"] = final_snapshot
            cleanup = observe_teardown(cleanup, epoch=epoch)
            final_snapshot = resource_snapshot(vault, db)
            report["final_resources"] = final_snapshot
            journal(output / "resources.jsonl", dict(final_snapshot))
            report["cleanup"] = asdict(cleanup)
            maintenance.end_restore_maintenance()
            counts = final_snapshot["database_counts"]
            retain = not cleanup.quiescent or any(
                counts[name]
                for name in (
                    "active_jobs",
                    "capacity_reservations",
                    "staging_leases",
                    "mesh_fingerprint_continuations",
                )
            )
            if retain:
                errors.append("private_drain_not_proven")
    except BaseException as exc:  # noqa: BLE001 - finite tooling boundary records cancellation too
        stop.set()
        errors.append(f"{type(exc).__name__}: {exc}")
    report["errors"] = [*errors, *sample_errors]
    report["elapsed_total_seconds"] = time.monotonic() - started
    report["workspace_retained"] = retain
    report["decision"] = (
        "failed"
        if errors or sample_errors
        else "evidence_complete_requires_resource_review"
    )
    if args.mode == Mode.SOAK:
        summary = summarize(samples, elapsed_seconds=soak_seconds)
        report["smoke_only"] = args.duration_seconds < 7200 or args.min_artifacts < 1000
        attained = summary["distinct_usable_artifacts"]
        if attained < args.min_artifacts or (
            not report["smoke_only"] and not summary["soak_thresholds_met"]
        ):
            report["decision"] = "failed"
            report["errors"] = [
                *errors,
                *sample_errors,
                "requested_soak_thresholds_not_met",
            ]
    retain = retain or report["decision"] == "failed"
    report["workspace_retained"] = retain
    if not retain:
        shutil.rmtree(workspace)
    atomic_report(output / "report.json", report)
    return 1 if report["decision"] == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.worker:
        return run_worker(args)
    args.output_dir.mkdir(parents=True, mode=0o700)
    atomic_report(
        args.output_dir / "report.json",
        {
            "schema_version": 1,
            "mode": args.mode.value,
            "decision": "pending",
            "workspace_retained": True,
            "errors": [],
            "summary": summarize([], elapsed_seconds=0),
        },
    )
    command = [
        sys.executable,
        "-m",
        "scripts.qualify_ingestion",
        *(sys.argv[1:] if argv is None else argv),
        "--worker",
    ]
    environment = {
        **os.environ,
        "PYTHONPATH": str(BACKEND) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    with (
        (args.output_dir / "child.stdout.log").open("w") as stdout,
        (args.output_dir / "child.stderr.log").open("w") as stderr,
    ):
        child = subprocess.Popen(
            command,
            cwd=BACKEND,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            code = child.wait(timeout=args.deadline_seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            import psutil

            try:
                descendants = psutil.Process(child.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for process in descendants:
                try:
                    process.terminate()
                except psutil.NoSuchProcess:
                    pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            _, alive = psutil.wait_procs(descendants, timeout=5)
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            report_path = args.output_dir / "report.json"
            report: dict[str, object] = (
                json.loads(report_path.read_text())
                if report_path.exists()
                else {"schema_version": 1}
            )
            prior_errors = report.get("errors", [])
            errors = (
                prior_errors
                if isinstance(prior_errors, list)
                else ["invalid_child_error_report"]
            )
            report.update(
                {
                    "decision": "failed",
                    "parent_failure": type(exc).__name__,
                    "errors": [
                        *errors,
                        "qualification_parent_deadline"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "qualification_interrupted",
                    ],
                    "workspace_retained": True,
                }
            )
            atomic_report(report_path, report)
            code = 1
        if code != 0:
            report_path = args.output_dir / "report.json"
            final_report = json.loads(report_path.read_text())
            if final_report.get("decision") != "failed":
                final_report.update(
                    decision="failed",
                    parent_failure=f"child_exit:{code}",
                    workspace_retained=True,
                )
                atomic_report(report_path, final_report)
    print(
        json.dumps({"report": str(args.output_dir / "report.json"), "exit_code": code})
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

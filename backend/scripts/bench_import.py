#!/usr/bin/env python3
"""Import an entire ZIP into a fresh local server and save comparable measurements.

Run from backend:
    uv run python scripts/bench_import.py /path/library.zip --output before.json
    uv run python scripts/bench_import.py /path/library.zip --output after.json --compare before.json

Each run uses a fresh migrated SQLite or PostgreSQL database and temporary local storage. Setup,
archive hashing, and server startup are excluded from the timed upload-to-completion
interval. All importable entries are selected. No existing server or vault is used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx
import psutil

if __package__ in (None, ""):
    # Preserve the documented `python scripts/bench_import.py` entry point.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bench_database import (
    database_record,
    disposable_database,
    metadata_catalog,
    pending_enrichment,
    read_rows,
)

_SETUP_RATE_LIMIT_BACKOFF_SECONDS = 61


def environment_record(backend: Path) -> dict:
    """Record reproducibility facts without changing host caches or resources."""
    revision = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=backend,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            revision = result.stdout.strip()
    except FileNotFoundError:
        pass  # Minimal runtime images need not contain the Git executable.
    limits = {}
    for name in ("cpu.max", "memory.max", "memory.swap.max"):
        try:
            limits[name] = (Path("/sys/fs/cgroup") / name).read_text().strip()
        except OSError:
            limits[name] = None
    versions = {}
    for name in ("numpy", "trimesh", "pillow", "cascadio", "printstash-mesh-native"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    process = psutil.Process()
    return {
        "run_id": str(uuid.uuid4()),
        "git_revision": revision,
        "dependency_versions": versions,
        "cpu_affinity": process.cpu_affinity()
        if hasattr(process, "cpu_affinity")
        else None,
        "cgroup_root_limits": limits,
        "cache_condition": "uncontrolled shared host cache; no global cache flush",
        "timing_excludes": ["archive hashing", "database migration", "server startup"],
    }


class ServerResources:
    """Sample the server throughout upload, extraction, and processing.

    Linux CPU totals include reaped subprocesses plus currently live descendants.
    RSS sums each live process (shared mappings may therefore be counted twice).
    The sampler runs in this client, outside the measured server process tree.
    """

    def __init__(self, process: psutil.Process):
        self.process = process
        self.done = threading.Event()
        self.peak_rss = 0
        self.rss = 0
        self.cpu = (0.0, 0.0)
        self.sample()
        self.initial_cpu = self.cpu
        self.initial_rss = self.rss
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def sample(self) -> None:
        rss = 0
        user = system = 0.0
        for process in [self.process, *self.process.children(recursive=True)]:
            try:
                with process.oneshot():
                    rss += process.memory_info().rss
                    cpu = process.cpu_times()
                    user += cpu.user + getattr(cpu, "children_user", 0.0)
                    system += cpu.system + getattr(cpu, "children_system", 0.0)
            except psutil.NoSuchProcess:
                continue
        self.rss = rss
        self.peak_rss = max(self.peak_rss, rss)
        self.cpu = (user, system)

    def _poll(self) -> None:
        while not self.done.wait(0.1):
            try:
                self.sample()
            except psutil.NoSuchProcess:
                return

    def stop(self) -> None:
        self.done.set()
        self.thread.join()

    def report(self, elapsed: float, *, stop: bool = True) -> dict:
        if stop:
            self.stop()
        self.sample()
        user = self.cpu[0] - self.initial_cpu[0]
        system = self.cpu[1] - self.initial_cpu[1]
        return {
            "resource_sample_interval_seconds": 0.1,
            "resource_measurement_scope": "upload through complete import; server and descendants",
            "sampled_peak_server_tree_rss_bytes": self.peak_rss,
            "initial_server_tree_rss_bytes": self.initial_rss,
            "server_tree_cpu_user_seconds": user,
            "server_tree_cpu_system_seconds": system,
            "server_tree_cpu_seconds": user + system,
            "server_tree_average_cpu_cores": (user + system) / elapsed,
        }


def body(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json()


def complete_setup(client: httpx.Client, csrf: str, payload: dict) -> dict:
    """Complete isolated setup, tolerating one expired in-process rate window.

    Setup precedes the measured interval. A bounded retry keeps a multi-hour
    matrix from losing all prior evidence to a transient 429 while preserving
    the production endpoint's limit and making repeated rejection terminal.
    """
    for attempt in range(2):
        response = client.post(
            "/api/v1/setup",
            headers={"X-PrintStash-Setup-CSRF": csrf},
            json=payload,
        )
        if response.status_code != 429 or attempt:
            return body(response)
        time.sleep(_SETUP_RATE_LIMIT_BACKOFF_SECONDS)
    raise AssertionError("bounded setup attempts exhausted")


def latency_summary(samples):
    values = sorted(sample["library_ms"] for sample in samples)
    if not values:
        return {}
    return {
        "samples": len(values),
        "median_ms": values[len(values) // 2],
        "p95_ms": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max_ms": values[-1],
    }


def run(
    archive: Path,
    output: Path,
    timeout: float,
    similarity: bool = False,
    export_previews: Path | None = None,
    workers: int = 1,
    database: str = "sqlite",
    postgres_admin_url: str | None = None,
) -> dict:
    backend = Path(__file__).resolve().parent.parent
    environment = environment_record(backend)
    engine_digest = hashlib.sha256()
    for directory in (backend / "app", backend / "packages/printstash-core/src"):
        for path in sorted(directory.rglob("*.py")):
            engine_digest.update(path.relative_to(backend).as_posix().encode() + b"\0")
            engine_digest.update(path.read_bytes())
    native_spec = importlib.util.find_spec("printstash_mesh_native")
    native_binary_spec = (
        importlib.util.find_spec("printstash_mesh_native.printstash_mesh_native")
        if native_spec
        else None
    )
    native_path = (
        Path(native_binary_spec.origin)
        if native_binary_spec and native_binary_spec.origin
        else None
    )
    if native_path is None:
        raise RuntimeError("Required printstash-mesh-native extension is not installed")
    with archive.open("rb") as source:
        archive_digest = hashlib.file_digest(source, "sha256").hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.with_suffix(".server.log")
    with (
        tempfile.TemporaryDirectory(prefix="printstash-import-bench-") as temporary,
        disposable_database(
            Path(temporary), database, postgres_admin_url=postgres_admin_url
        ) as database_engine,
    ):
        root = Path(temporary)
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("VAULT_")
        }
        env["PYTHONPATH"] = os.pathsep.join(
            (str(backend), str(backend / "packages/printstash-core/src"))
        )
        env.update(
            {
                "VAULT_DB_URL": database_engine.url.render_as_string(
                    hide_password=False
                ),
                "VAULT_JWT_SECRET": secrets.token_urlsafe(48),
                "VAULT_SECRETS_KEY": secrets.token_urlsafe(48),
                "VAULT_SECRETS_KEY_FILE": str(root / "secrets.key"),
                "VAULT_SETUP_MODE": "trusted_network",
                "VAULT_SETUP_ALLOWED_HOSTS": "127.0.0.1",
                "VAULT_IMPORT_WORKERS": str(workers),
                "VAULT_ARTIFACT_CACHE_ROOT": str(root / "artifact-cache"),
            }
        )
        for key, folder in (
            ("DATA", "files"),
            ("THUMB", "thumbs"),
            ("STAGING", "staging"),
            ("BACKUP", "backups"),
        ):
            path = root / folder
            path.mkdir()
            env[f"VAULT_{key}_DIR"] = str(path)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        with log_path.open("w") as log:
            subprocess.run(
                [sys.executable, "-m", "app.db.migrate"],
                cwd=backend,
                env=env,
                stdout=log,
                stderr=log,
                check=True,
            )
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=backend,
                env=env,
                stdout=log,
                stderr=log,
            )
            resources = None
            try:
                process = psutil.Process(server.pid)
                with httpx.Client(
                    base_url=origin, timeout=timeout, headers={"Origin": origin}
                ) as client:
                    ready_deadline = time.monotonic() + 90
                    while True:
                        if server.poll() is not None:
                            raise RuntimeError(f"Server stopped; see {log_path}")
                        try:
                            if client.get("/api/v1/setup/status", timeout=1).is_success:
                                break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() >= ready_deadline:
                            raise TimeoutError("Server startup timed out")
                        time.sleep(0.2)
                    csrf = body(client.post("/api/v1/setup/session"))["csrf"]
                    setup = complete_setup(
                        client,
                        csrf,
                        {
                            "username": "benchmark",
                            "password": secrets.token_urlsafe(24),
                            "data_dir": str(root / "files"),
                            "thumb_dir": str(root / "thumbs"),
                        },
                    )
                    client.headers["Authorization"] = "Bearer " + setup["access_token"]
                    if similarity:
                        body(
                            client.patch(
                                "/api/v1/similarity/settings",
                                json={"enabled": True, "fingerprint_on_ingest": True},
                            )
                        )
                    resources = ServerResources(process)
                    start = time.monotonic()
                    with archive.open("rb") as source:
                        manifest = body(
                            client.post(
                                "/api/v1/ingest/archive",
                                files={
                                    "file": (archive.name, source, "application/zip")
                                },
                            )
                        )
                    uploaded = time.monotonic()
                    names = [
                        entry["name"]
                        for entry in manifest["entries"]
                        if entry["file_type"]
                    ]
                    job = body(
                        client.post(
                            f"/api/v1/ingest/archive/{manifest['archive_id']}/select",
                            json={"names": names},
                        )
                    )
                    accepted = time.monotonic()
                    samples = []
                    navigation_samples = []
                    last_print = 0.0
                    while True:
                        now = time.monotonic()
                        if now - start > timeout:
                            raise TimeoutError("Complete archive import timed out")
                        poll_start = time.monotonic()
                        status = body(
                            client.get(
                                f"/api/v1/ingest/jobs/{job['job_id']}", timeout=30
                            )
                        )
                        sample = {
                            "seconds": round(time.monotonic() - start, 4),
                            "server_tree_rss_bytes": resources.rss,
                            "poll_ms": round((time.monotonic() - poll_start) * 1000, 3),
                            **{
                                key: status.get(key)
                                for key in (
                                    "state",
                                    "stage",
                                    "current_item",
                                    "processed",
                                    "total",
                                    "progress",
                                )
                            },
                        }
                        probe = time.monotonic()
                        body(client.get("/api/v1/models", params={"limit": 20}))
                        navigation_samples.append(
                            {
                                "seconds": time.monotonic() - start,
                                "phase": "ingestion",
                                "library_ms": (time.monotonic() - probe) * 1000,
                            }
                        )
                        samples.append(sample)
                        if now - last_print >= 10 or status["state"] in (
                            "completed",
                            "failed",
                        ):
                            print(json.dumps(sample), flush=True)
                            last_print = now
                        if status["state"] in ("completed", "failed"):
                            break
                        time.sleep(0.25)
                    saved = time.monotonic()
                    saved_resources = resources.report(saved - start, stop=False)
                    enrichment_samples = []
                    while True:
                        if time.monotonic() - start > timeout:
                            raise TimeoutError("Background enrichment timed out")
                        with database_engine.connect() as db:
                            outstanding, failed_enrichment = pending_enrichment(
                                db, similarity=similarity
                            )
                        probe = time.monotonic()
                        body(client.get("/api/v1/models", params={"limit": 20}))
                        enrichment_samples.append(
                            {
                                "seconds": time.monotonic() - start,
                                "pending": outstanding,
                                "library_ms": (time.monotonic() - probe) * 1000,
                            }
                        )
                        navigation_samples.append(
                            {**enrichment_samples[-1], "phase": "enrichment"}
                        )
                        if not outstanding:
                            break
                        time.sleep(0.25)
                    finished = time.monotonic()
                    resource_report = resources.report(finished - start)
                    with database_engine.connect() as db:
                        database_details = database_record(db)
                        parsed_metadata = metadata_catalog(db)
                        catalog = read_rows(
                            db,
                            "SELECT sha256, size_bytes FROM files ORDER BY sha256, size_bytes",
                        )
                        artifact_jobs = [
                            json.loads(row[0])
                            for row in read_rows(
                                db,
                                "SELECT status_json FROM background_jobs WHERE kind = 'artifact'",
                            )
                        ]
                        preview_outcomes = read_rows(
                            db,
                            "SELECT t.source_sha256, f.file_type, t.state, t.failure_reason "
                            "FROM thumbnail_generations t JOIN files f ON f.id = t.file_id "
                            "ORDER BY t.source_sha256, f.file_type, t.state, t.failure_reason",
                        )
                        previews = read_rows(
                            db,
                            "SELECT source_sha256, output_sha256, state FROM thumbnail_generations "
                            "ORDER BY source_sha256, output_sha256, state",
                        )
                        geometry_catalog = read_rows(
                            db,
                            "SELECT f.sha256, m.bbox_x_mm, m.bbox_y_mm, m.bbox_z_mm, "
                            "m.volume_mm3, m.triangle_count FROM files f "
                            "LEFT JOIN metadata m ON m.file_id = f.id ORDER BY f.sha256",
                        )
                        fingerprints = []
                        missing_fingerprints = []
                        if similarity:
                            fingerprints = read_rows(
                                db,
                                "SELECT source_sha256, component_index, algorithm_version, state, "
                                "component_count, instance_count, face_count, vertex_count, "
                                "physical_hash_0, physical_hash_1, physical_hash_2, physical_hash_3, "
                                "normalized_hash_0, normalized_hash_1, normalized_hash_2, normalized_hash_3 "
                                "FROM geometry_fingerprints ORDER BY source_sha256, component_index, algorithm_version",
                            )
                            expected_sources = {
                                row[0]
                                for row in read_rows(
                                    db,
                                    "SELECT sha256 FROM files WHERE file_type != 'GCODE'",
                                )
                            }
                            missing_fingerprints = sorted(
                                expected_sources - {row[0] for row in fingerprints}
                            )
                        preview_files = read_rows(
                            db,
                            "SELECT source_sha256, storage_key, state FROM thumbnail_generations "
                            "ORDER BY source_sha256, state",
                        )
                    # Decode only after the timed interval and resource snapshot.
                    # Pixel comparison permits a lossless encoder change without
                    # treating different compressed bytes as different images.
                    from PIL import Image

                    pixel_catalog = []
                    preview_bytes = 0
                    if export_previews is not None:
                        export_previews.mkdir(parents=True, exist_ok=True)
                    for source_hash, key, state in preview_files:
                        if key is None:
                            pixel_catalog.append((source_hash, None, None, None, state))
                            continue
                        path = Path(key).resolve()
                        if not path.is_relative_to(root.resolve()):
                            raise ValueError(
                                "Preview escaped the temporary benchmark vault"
                            )
                        encoded = path.read_bytes()
                        preview_bytes += len(encoded)
                        with Image.open(path) as preview:
                            rgba = preview.convert("RGBA")
                            pixel_catalog.append(
                                (
                                    source_hash,
                                    hashlib.sha256(rgba.tobytes()).hexdigest(),
                                    rgba.width,
                                    rgba.height,
                                    state,
                                )
                            )
                        if export_previews is not None:
                            digest = hashlib.sha256(encoded).hexdigest()
                            (
                                export_previews / f"{source_hash}-{digest}.webp"
                            ).write_bytes(encoded)
                    report = {
                        **environment,
                        "native_capabilities": sorted(
                            name
                            for name in dir(
                                importlib.import_module("printstash_mesh_native")
                            )
                            if not name.startswith("_")
                        ),
                        "similarity_on_ingest": similarity,
                        "engine_source_sha256": engine_digest.hexdigest(),
                        "preview_profile_sha256": hashlib.sha256(
                            (
                                backend
                                / "packages/printstash-core/src/printstash_core/mesh/preview_profile.json"
                            ).read_bytes()
                        ).hexdigest(),
                        "import_workers_requested": workers,
                        "import_admission": [
                            {"workers": int(count), "memory_budget_bytes": int(budget)}
                            for count, budget in re.findall(
                                r"mesh_import_admission workers=(\d+) memory_budget_bytes=(\d+)",
                                log_path.read_text(),
                            )
                        ],
                        "renderer_selected": "rust",
                        "loader_selected": "rust",
                        "geometry_selected": "rust",
                        "native_module_sha256": hashlib.sha256(
                            native_path.read_bytes()
                        ).hexdigest(),
                        "archive": archive.name,
                        "archive_sha256": archive_digest,
                        "archive_bytes": archive.stat().st_size,
                        "selected_files": len(names),
                        "platform": platform.platform(),
                        "python": platform.python_version(),
                        "cpu_count": os.cpu_count(),
                        "storage": "local filesystem",
                        "database": database_details,
                        "upload_seconds": uploaded - start,
                        "extraction_seconds": accepted - uploaded,
                        "processing_seconds": finished - accepted,
                        "total_seconds": finished - start,
                        "saved_seconds": saved - start,
                        "measurement_protocol": "job-and-library-poll-250ms-v5",
                        "failed_enrichment": failed_enrichment,
                        "navigation_samples": navigation_samples,
                        "navigation_latency": latency_summary(navigation_samples),
                        "background_after_save_seconds": finished - saved,
                        "saved_resources": saved_resources,
                        "enrichment_samples": enrichment_samples,
                        **resource_report,
                        "catalog": catalog,
                        "geometry_catalog": geometry_catalog,
                        "metadata_catalog": parsed_metadata,
                        "fingerprint_catalog": fingerprints,
                        "missing_fingerprint_sources": missing_fingerprints,
                        "status": status,
                        "samples": samples,
                        "artifact_jobs": artifact_jobs,
                        "preview_catalog": previews,
                        "preview_outcome_catalog": preview_outcomes,
                        "preview_pixel_catalog": sorted(pixel_catalog),
                        "preview_bytes": preview_bytes,
                        "thumbnail_states_at_import_completion": dict(
                            Counter(
                                job.get("thumbnail_status") for job in artifact_jobs
                            )
                        ),
                        "fingerprint_states_at_import_completion": dict(
                            Counter(
                                job.get("fingerprint_status") or "not_requested"
                                for job in artifact_jobs
                            )
                        ),
                    }
                    output.write_text(json.dumps(report, indent=2) + "\n")
                    if (
                        status["state"] != "completed"
                        or status["failed"]
                        or status["processed"] != len(names)
                        or len(catalog) != len(names)
                        or any(failed_enrichment.values())
                        or missing_fingerprints
                        or any(row[3] in {"pending", "failed"} for row in fingerprints)
                    ):
                        raise RuntimeError(
                            f"Archive did not import completely; see {output}"
                        )
                    return report
            finally:
                if resources is not None:
                    resources.stop()
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


def compare_preview_outcomes(before: dict, after: dict) -> None:
    key = "preview_outcome_catalog"
    if key not in before or key not in after:
        raise SystemExit("Comparison refused: preview outcome evidence is missing")
    if before[key] != json.loads(json.dumps(after[key])):
        raise SystemExit("Comparison refused: preview outcomes differ")


def compare_previews(before: dict, after: dict, *, mode: str = "bytes") -> None:
    """Require identical stored images, allowing decoded comparison explicitly."""
    key = "preview_pixel_catalog" if mode == "pixels" else "preview_catalog"
    if mode == "pixels" and key not in before:
        raise SystemExit("Comparison refused: reference has no decoded pixel catalog")
    if key in before and before[key] != json.loads(json.dumps(after[key])):
        raise SystemExit(
            f"Comparison refused: generated preview {mode} differ; inspect both reports"
        )


def compare_metadata(before: dict, after: dict) -> None:
    if "metadata_catalog" not in before or "metadata_catalog" not in after:
        raise SystemExit("Comparison refused: parsed metadata evidence is missing")
    if before["metadata_catalog"] != after["metadata_catalog"]:
        raise SystemExit("Comparison refused: parsed metadata differs")


def compare_fingerprints(before: dict, after: dict) -> None:
    """Compare final component identity and readiness, never initial job hints."""
    if "fingerprint_catalog" not in before or "fingerprint_catalog" not in after:
        raise SystemExit("Comparison refused: final fingerprint catalog is missing")
    if before["fingerprint_catalog"] != json.loads(
        json.dumps(after["fingerprint_catalog"])
    ):
        raise SystemExit("Comparison refused: final similarity fingerprints differ")


def compare_databases(before: dict, after: dict) -> None:
    if not before.get("database") or before["database"] != after.get("database"):
        raise SystemExit("Comparison refused: database backend or version differs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--database", choices=("sqlite", "postgres"), default="sqlite")
    parser.add_argument(
        "--postgres-admin-url-env",
        help="Environment variable holding an isolated PostgreSQL test server's maintenance URL",
    )
    parser.add_argument(
        "--preview-comparison", choices=("bytes", "pixels"), default="bytes"
    )
    parser.add_argument("--export-previews", type=Path)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--workers", type=int, choices=range(33), default=1)
    parser.add_argument(
        "--similarity",
        action="store_true",
        help="Enable analysis on upload",
    )
    args = parser.parse_args()
    postgres_admin_url = None
    if args.postgres_admin_url_env:
        if args.database != "postgres":
            parser.error("--postgres-admin-url-env requires --database postgres")
        postgres_admin_url = os.environ.get(args.postgres_admin_url_env)
        if not postgres_admin_url:
            parser.error(
                "PostgreSQL test server environment variable is empty or missing"
            )
    before = None
    if args.compare:
        before = json.loads(args.compare.read_text())
        reference_status = before.get("status", {})
        if (
            not isinstance(reference_status, dict)
            or reference_status.get("state") != "completed"
            or reference_status.get("failed", 0)
            or reference_status.get("processed") != before.get("selected_files")
        ):
            raise SystemExit(
                "Comparison refused: reference import did not complete successfully"
            )
        if before.get("measurement_protocol") != "job-and-library-poll-250ms-v5":
            raise SystemExit("Comparison refused: measurement protocols differ")
        if before.get("similarity_on_ingest", False) != args.similarity:
            raise SystemExit("Comparison refused: similarity settings differ")
        if (
            args.preview_comparison == "pixels"
            and "preview_pixel_catalog" not in before
        ):
            raise SystemExit(
                "Comparison refused: reference has no decoded pixel catalog"
            )
    report = run(
        args.archive.resolve(),
        args.output.resolve(),
        args.timeout,
        args.similarity,
        args.export_previews,
        args.workers,
        args.database,
        postgres_admin_url,
    )
    print(
        f"Complete archive: {report['selected_files']} files in {report['total_seconds']:.2f}s"
    )
    print(
        f"Server CPU: {report['server_tree_cpu_seconds']:.2f}s; "
        f"sampled peak RSS: {report['sampled_peak_server_tree_rss_bytes'] / 1024**2:.1f} MiB"
    )
    if before is not None:
        compare_databases(before, report)
        compare_metadata(before, report)
        if (before["archive_sha256"], before["catalog"]) != (
            report["archive_sha256"],
            json.loads(json.dumps(report["catalog"])),
        ):
            raise SystemExit(
                "Comparison refused: input archive or imported file inventory differs"
            )
        if "geometry_catalog" in before and before["geometry_catalog"] != json.loads(
            json.dumps(report["geometry_catalog"])
        ):
            raise SystemExit(
                "Comparison refused: imported geometry measurements differ; inspect both reports"
            )
        compare_preview_outcomes(before, report)
        compare_previews(before, report, mode=args.preview_comparison)
        if args.similarity:
            compare_fingerprints(before, report)
        print(
            f"Before {before['total_seconds']:.2f}s; after {report['total_seconds']:.2f}s; speedup {before['total_seconds'] / report['total_seconds']:.2f}x"
        )


if __name__ == "__main__":
    main()

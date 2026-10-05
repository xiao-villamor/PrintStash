"""Measure frozen mesh inputs through real workers or production DBOS ingestion.

Run python -m scripts.bench_mesh_pipeline --mode worker --runs 1.
Ingestion mode creates a new private local vault; it never modifies a configured
installation. Its HTTP requests use ASGI transport, so network latency is excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import shutil
import sys
import tempfile
from contextlib import chdir, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.benchmark_environment import collect_environment
from scripts.benchmark_pipeline_contracts import (
    BenchmarkMode,
    InputIdentity,
    SampleOutcome,
)
from scripts.mesh_benchmark_corpus import build_contract_corpus as build_v1
from scripts.mesh_benchmark_corpus_v2 import build_contract_corpus as build_v2

if TYPE_CHECKING:
    from app.core.config import ConfigResolver


def configure_private_vault(root: Path) -> None:
    # Environment is set before any application import creates Settings. The
    # benchmark always owns a fresh temporary vault, independently of the shell.
    for name in tuple(os.environ):
        if name.startswith("VAULT_"):
            del os.environ[name]
    os.environ["VAULT_DATA_ROOT"] = str(root)
    os.environ["VAULT_STORAGE_BACKEND"] = "local"
    os.environ["VAULT_PROCESS_ROLE"] = "all"
    os.environ["VAULT_SETUP_MODE"] = "trusted_network"
    os.environ["VAULT_SETUP_ALLOWED_HOSTS"] = "testserver"
    os.environ["VAULT_API_RUNS_JOBS"] = "true"
    os.environ["VAULT_LOG_LEVEL"] = "WARNING"
    os.environ["VAULT_SIMILARITY_ENABLED"] = "false"
    os.environ["VAULT_SIMILARITY_FINGERPRINT_ON_INGEST"] = "false"


def export_private_settings(settings: ConfigResolver) -> None:
    """Children start in the backend directory: pin their private defaults too.

    The parent has already loaded Settings in an empty directory with no inherited
    VAULT variables. Export that snapshot so a child's local .env cannot change
    output geometry, budgets or owned paths. Never put these values in reports.
    """
    for name, value in settings.frozen.model_dump(
        mode="json", exclude_none=True
    ).items():
        if isinstance(value, str):
            serialized = value
        else:
            serialized = json.dumps(value, allow_nan=False)
        os.environ["VAULT_" + name.upper()] = serialized
    from app.bootstrap.native_resources import configure as configure_native_resources
    from app.modules.media.mesh_policy import render_jobs_limit

    # Worker-only benchmarks never enter the application's lifespan. Compose
    # the same resource owners explicitly under the exported private vault.
    private_root = Path(os.environ["VAULT_DATA_ROOT"])
    configure_native_resources(private_root)

    # None means an adaptive limit, so exporting no value would allow the
    # child's .env to replace that policy. Pin the resolved private limit.
    os.environ["VAULT_JOBS_DERIVE_NATIVE_CONCURRENCY"] = str(render_jobs_limit())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", type=BenchmarkMode, choices=list(BenchmarkMode), required=True
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--corpus", choices=("v1", "v2"), default="v1")
    parser.add_argument(
        "--full", action="store_true", help="Materialize v2 inputs up to 2M faces"
    )
    parser.add_argument(
        "--download-external",
        action="store_true",
        help="Fetch hash-pinned real slicer inputs in v2",
    )
    parser.add_argument("--deadline-seconds", type=float, default=180)
    args = parser.parse_args()
    if args.corpus == "v1" and (args.full or args.download_external):
        parser.error("full/download profiles require corpus v2")
    if args.runs < 1:
        parser.error("run counts must be positive")
    if not math.isfinite(args.deadline_seconds) or args.deadline_seconds < 0:
        parser.error("deadline must be nonnegative")
    environment = collect_environment()
    root = Path(tempfile.mkdtemp(prefix="printstash-pipeline-benchmark-"))
    retain_workspace = False
    try:
        manifest = (
            build_v1(root / "inputs")
            if args.corpus == "v1"
            else build_v2(
                root / "inputs",
                full=args.full,
                download_external=args.download_external,
            )
        )
        by_name = {entry.filename: entry for entry in manifest.fixtures}
        names = args.cases if args.cases is not None else list(by_name)
        if any(name not in by_name for name in names):
            parser.error("unknown corpus case")
        configure_private_vault(root / "vault")
        work = []
        for name in names:
            path = root / "inputs" / name
            entry = by_name[name]
            with path.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != entry.sha256 or path.stat().st_size != entry.input_bytes:
                raise RuntimeError("frozen corpus identity mismatch")
            for index in range(1, args.runs + 1):
                work.append(
                    (path, InputIdentity(name, actual, entry.input_bytes, index))
                )
        # Redirect production stdout diagnostics away from the sole JSON report.
        with redirect_stdout(sys.stderr), chdir(root):
            from app.core.config import settings
            from app.modules.derivatives.kinds import (
                MESH_GEOMETRY_RECIPE,
                MESH_THUMBNAIL_RECIPE,
            )

            export_private_settings(settings)

            for handler in logging.getLogger().handlers:
                if (
                    isinstance(handler, logging.StreamHandler)
                    and handler.stream is sys.stdout
                ):
                    handler.setStream(sys.stderr)
            if args.mode == BenchmarkMode.WORKER:
                from scripts.benchmark_native import measure_worker

                samples = [measure_worker(path, identity) for path, identity in work]
                bootstrap_ms = None
                cleanup = None
            else:
                from scripts.benchmark_ingestion import measure_ingestions

                # An unhandled bootstrap/teardown failure must preserve evidence
                # too: only proven physical quiescence permits deletion.
                retain_workspace = True
                samples, bootstrap_ms, cleanup = measure_ingestions(
                    work, deadline_seconds=args.deadline_seconds
                )
                retain_workspace = not cleanup.quiescent
            recipes = {
                "metadata": MESH_GEOMETRY_RECIPE,
                "thumbnail": MESH_THUMBNAIL_RECIPE,
            }
            dimensions = {
                "width": settings.model_thumbnail_width,
                "height": round(settings.model_thumbnail_width * 3 / 4),
            }
        samples_json = [asdict(sample) for sample in samples]
        report = {
            "schema_version": 1,
            "mode": args.mode,
            "environment": asdict(environment),
            "corpus_manifest": asdict(manifest),
            "recipes": recipes,
            "output_dimensions": dimensions,
            "runs": args.runs,
            "bootstrap_ms": bootstrap_ms,
            "cleanup": asdict(cleanup) if cleanup is not None else None,
            "workspace_retained": str(root) if retain_workspace else None,
            "protocol": {
                "sample_order": "corpus_selector_then_repetition",
                "filesystem_cache": "uncontrolled",
                "worker_process": "fresh_per_sample"
                if args.mode == BenchmarkMode.WORKER
                else "fresh_per_derivation",
                "includes_http": args.mode == BenchmarkMode.INGESTION,
                "http_transport": "asgi_no_network"
                if args.mode == BenchmarkMode.INGESTION
                else None,
                "includes_database": args.mode == BenchmarkMode.INGESTION,
                "includes_worker_startup": True,
                "ingestion_repetitions": "new_upload_same_bytes_shared_private_vault",
                "availability_timestamps": "first_observed_upper_bounds"
                if args.mode == BenchmarkMode.INGESTION
                else None,
                "poll_interval_seconds": 0.05
                if args.mode == BenchmarkMode.INGESTION
                else None,
                "fingerprint_requested": False,
                "configuration": "isolated_production_defaults",
                "job_engine": "dbos" if args.mode == BenchmarkMode.INGESTION else None,
                "native_math_threads": 1,
                "native_deadline_seconds": settings.mesh_worker_timeout_seconds,
                "availability_deadline_seconds": args.deadline_seconds
                if args.mode == BenchmarkMode.INGESTION
                else None,
                "sample_elapsed_excludes_original_verification": True,
                "native_phase_collection": "per_sample_reply"
                if args.mode == BenchmarkMode.WORKER
                else "none",
                "output_boundary": "raw_worker_reply"
                if args.mode == BenchmarkMode.WORKER
                else "canonical_visible_thumbnail",
                "timeout_carryover": "previous_jobs_can_remain_active_in_shared_vault"
                if args.mode == BenchmarkMode.INGESTION
                else None,
            },
            "samples": samples_json,
            "summary": {
                "sample_count": len(samples),
                **{
                    outcome.value: sum(sample.outcome == outcome for sample in samples)
                    for outcome in SampleOutcome
                },
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return 1 if retain_workspace else 0
    finally:
        if retain_workspace:
            print(f"Benchmark workspace retained: {root}", file=sys.stderr)
        else:
            shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())

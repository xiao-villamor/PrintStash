#!/usr/bin/env python3
"""Measure uncached engine renders and local persisted-thumbnail delivery reads.

Run ``uv run python -m scripts.bench_thumbnails --quick`` from backend/.
Render samples share an interpreter; filesystem caches are uncontrolled. This
microbenchmark does not measure HTTP, database lookup, or worker startup costs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import resource
import statistics
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import trimesh
from trimesh.exchange.stl import export_stl

from app.core.config import settings
from app.modules.derivatives.kinds import MESH_THUMBNAIL_RECIPE
from app.modules.media.mesh_contracts import ThumbnailRequest, ThumbnailStrategy
from app.modules.media.mesh_telemetry import PhaseStats
from app.modules.media.thumbnail import to_webp
from app.modules.media.thumbnail_engine import ThumbnailEngine
from app.modules.storage.artifact_delivery import (
    DeliveryPurpose,
    DeliveryRequest,
    plan_stored_representation,
)
from app.modules.storage.storage_backend.local import (
    LocalStorageBackend,
    enroll_legacy_local_root,
)
from scripts.benchmark_environment import collect_environment
from scripts.mesh_benchmark_corpus import build_contract_corpus


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


@dataclass(frozen=True)
class Sample:
    sample_index: int
    elapsed_ms: float
    output_bytes: int
    output_sha256: str | None
    peak_rss_bytes: int
    error: str | None


@dataclass(frozen=True)
class RenderSample(Sample):
    strategy: ThumbnailStrategy
    phase_stats: tuple[PhaseStats, ...]


@dataclass(frozen=True)
class Measurement:
    name: str
    input_bytes: int
    input_sha256: str
    renders: list[RenderSample]
    representation_reads: list[Sample]
    render_median_ms: float | None
    representation_read_median_ms: float | None
    publication_error: str | None


def _successful_median(samples: list[Sample] | list[RenderSample]) -> float | None:
    timings = [sample.elapsed_ms for sample in samples if sample.error is None]
    return statistics.median(timings) if timings else None


def _export(mesh: trimesh.Trimesh, path: Path) -> None:
    path.write_bytes(export_stl(mesh))


def build_corpus(root: Path, *, quick: bool) -> list[Path]:
    corpus: list[Path] = []
    shapes = {
        "cube": trimesh.creation.box((20, 20, 20)),
        "flat": trimesh.creation.box((80, 50, 0.5)),
        "tall": trimesh.creation.box((10, 12, 120)),
        "wide": trimesh.creation.box((140, 15, 8)),
        "asymmetric": trimesh.util.concatenate(
            [
                trimesh.creation.box((50, 20, 8)),
                trimesh.creation.box((12, 12, 45)).apply_translation((18, 3, 22)),
            ]
        ),
        "dense": trimesh.creation.icosphere(subdivisions=2 if quick else 5, radius=30),
    }
    for name, mesh in shapes.items():
        path = root / f"{name}.stl"
        _export(mesh, path)
        corpus.append(path)

    hostile = root / "ambiguous-preview.3mf"
    with zipfile.ZipFile(hostile, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Metadata/one.png", b"not-a-preview")
        archive.writestr("Metadata/two.png", b"not-a-preview")
        archive.writestr("3D/3dmodel.model", b"<model/>")
    corpus.append(hostile)
    return corpus


def benchmark_file(path: Path, *, cold_runs: int, warm_runs: int) -> Measurement:
    """Record every attempt; failures never masquerade as faster successful work.

    The CLI's historical cold/warm flags mean uncached render / persisted read.
    Publishing the final successful output is setup for the read measurements.
    A failed final attempt has no representation to read and is never cached.
    """
    if cold_runs < 1 or warm_runs < 1:
        raise ValueError("run counts must be positive")
    with path.open("rb") as source:
        source_hash = hashlib.file_digest(source, "sha256").hexdigest()
    renders: list[RenderSample] = []
    reads: list[Sample] = []
    output: bytes | None = None
    for sample_index in range(1, cold_runs + 1):
        started = time.perf_counter()
        output = None
        error = None
        strategy = ThumbnailStrategy.NONE
        phase_stats: tuple[PhaseStats, ...] = ()
        try:
            result = ThumbnailEngine().generate(
                ThumbnailRequest(
                    path=path,
                    include_geometry=False,
                    reason="benchmark",
                    output_format="WEBP",
                )
            )
            strategy = result.strategy
            phase_stats = result.phase_stats
            if result.image is None:
                error = (
                    result.failure_reason.value
                    if result.failure_reason is not None
                    else "renderer_no_output"
                )
            else:
                output = to_webp(result.image)
        except Exception as exc:
            # The benchmark's contract is to retain failed attempts and continue
            # the corpus, including unexpected engine bugs such as AttributeError.
            error = f"{type(exc).__name__}: {exc}"
            logging.exception("benchmark render failed: %s", path.name)
        elapsed_ms = (time.perf_counter() - started) * 1000
        renders.append(
            RenderSample(
                sample_index=sample_index,
                elapsed_ms=elapsed_ms,
                output_bytes=len(output) if output is not None else 0,
                output_sha256=hashlib.sha256(output).hexdigest()
                if output is not None
                else None,
                peak_rss_bytes=_peak_rss_bytes(),
                error=error,
                strategy=strategy,
                phase_stats=phase_stats,
            )
        )

    publication_error = None
    if output is not None:
        with tempfile.TemporaryDirectory(
            prefix="printstash-bench-representation-"
        ) as raw:
            root = Path(raw)
            for directory in ("files", "thumbs", "backups"):
                (root / directory).mkdir()
            backend = LocalStorageBackend(
                data_dir=root / "files",
                thumb_dir=root / "thumbs",
                backup_dir=root / "backups",
            )
            key = backend.thumbnail_key(1)
            try:
                # These roots are new, private temporary storage, so enrollment
                # uses the same explicit empty-root contract as a fresh install.
                if not enroll_legacy_local_root(
                    backend.thumb_dir,
                    role="thumb",
                    installation=backend.storage_target.endpoint,
                    proofs=[],
                    allow_empty=True,
                ):
                    raise RuntimeError("benchmark thumbnail root enrollment failed")
                backend.write_bytes(output, key)
            except Exception as exc:
                # Storage setup failures are report data too; no invented hit.
                publication_error = f"{type(exc).__name__}: {exc}"
                logging.exception("benchmark publication failed: %s", path.name)
            else:
                for sample_index in range(1, warm_runs + 1):
                    started = time.perf_counter()
                    content: bytes | None = None
                    error = None
                    try:
                        plan = plan_stored_representation(
                            backend,
                            key,
                            DeliveryRequest(
                                filename="thumbnail.webp",
                                media_type="image/webp",
                                purpose=DeliveryPurpose.THUMBNAIL,
                            ),
                        )
                        try:
                            if plan.status != 200 or plan.path is None:
                                raise RuntimeError(
                                    f"unexpected local delivery plan: {plan.status}"
                                )
                            content = plan.path.read_bytes()
                            if content != output:
                                raise RuntimeError(
                                    "stored representation differs from render"
                                )
                        finally:
                            if plan.close is not None:
                                plan.close()
                    except Exception as exc:
                        # Retain each failed read, independently of successful ones.
                        error = f"{type(exc).__name__}: {exc}"
                        logging.exception(
                            "benchmark representation read failed: %s", path.name
                        )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    reads.append(
                        Sample(
                            sample_index=sample_index,
                            elapsed_ms=elapsed_ms,
                            output_bytes=len(content) if content is not None else 0,
                            output_sha256=hashlib.sha256(content).hexdigest()
                            if content is not None
                            else None,
                            peak_rss_bytes=_peak_rss_bytes(),
                            error=error,
                        )
                    )

    return Measurement(
        name=path.name,
        input_bytes=path.stat().st_size,
        input_sha256=source_hash,
        renders=renders,
        representation_reads=reads,
        render_median_ms=_successful_median(renders),
        representation_read_median_ms=_successful_median(reads),
        publication_error=publication_error,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cold-runs",
        type=int,
        default=5,
        help="uncached renders in one interpreter (not cold OS caches)",
    )
    parser.add_argument(
        "--warm-runs",
        type=int,
        default=20,
        help="local persisted representation reads, including the full body",
    )
    parser.add_argument("--label", default="working-tree")
    parser.add_argument("--external-model", type=Path)
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--quick", action="store_true")
    profile.add_argument(
        "--contract-corpus",
        action="store_true",
        help="use frozen contract inputs; target expectations do not imply parser compliance",
    )
    args = parser.parse_args()
    if args.cold_runs < 1 or args.warm_runs < 1:
        parser.error("run counts must be positive")
    if args.external_model is not None and not args.external_model.is_file():
        parser.error("external model must be an existing file")

    # The application logger defaults to stdout. Keep diagnostics off the JSON
    # channel, including the malformed corpus case's real renderer warnings.
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.setStream(sys.stderr)

    environment = collect_environment()
    with tempfile.TemporaryDirectory(prefix="printstash-thumbnail-bench-") as raw:
        from app.bootstrap.native_resources import configure

        configure(Path(raw) / "native-runtime")
        manifest = build_contract_corpus(Path(raw)) if args.contract_corpus else None
        corpus = (
            [Path(raw) / entry.filename for entry in manifest.fixtures]
            if manifest is not None
            else build_corpus(Path(raw), quick=args.quick)
        )
        if args.external_model is not None:
            corpus.append(args.external_model.resolve())
        measurements = [
            benchmark_file(path, cold_runs=args.cold_runs, warm_runs=args.warm_runs)
            for path in corpus
        ]
    payload = {
        "schema_version": 4,
        "environment": asdict(environment),
        "corpus_manifest": asdict(manifest) if manifest is not None else None,
        "output_dimensions": {
            "width": settings.model_thumbnail_width,
            "height": round(settings.model_thumbnail_width * 3 / 4),
        },
        "label": args.label,
        "mesh_thumbnail_recipe": MESH_THUMBNAIL_RECIPE,
        "recipes": {
            "mesh_thumbnail": MESH_THUMBNAIL_RECIPE,
            "mesh_metadata": None,
            "fingerprint": None,
        },
        "request": {
            "include_geometry": False,
            "include_fingerprint": False,
            "include_thumbnail": True,
            "output_format": "WEBP",
        },
        "corpus_order": [measurement.name for measurement in measurements],
        "cold_runs": args.cold_runs,
        "warm_runs": args.warm_runs,
        "protocol": {
            "render": "uncached_engine_same_interpreter",
            "representation_read": "local_storage_delivery_plan_and_full_body_read",
            "filesystem_cache": "uncontrolled",
            "sample_order": "corpus_sequential_then_renders_then_publication_then_reads",
            "representation_cache": "final_render_published_to_private_local_storage",
            "temporary_storage": "per_file_removed_after_reads",
            "publication": "setup_excluded_from_sample_timings",
            "median_scope": "successful_attempts_only_raw_failures_retained",
            "phase_scope": "engine_spans_inclusive_not_additive_to_elapsed_ms",
            "phase_bytes": "known_logical_sizes_not_measured_io",
            "rss_scope": "process_lifetime_high_water_self",
            "includes_http": False,
            "includes_database": False,
            "includes_worker_startup": False,
        },
        "measurements": [asdict(item) for item in measurements],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

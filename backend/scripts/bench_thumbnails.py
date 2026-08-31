#!/usr/bin/env python3
"""Reproducible thumbnail cold-render and warm-cache benchmark.

Run this script from each checkout being compared; timing thresholds deliberately
stay out of CI. An external report model can be supplied locally without adding
an unlicensed binary to the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Protocol

import trimesh

from app.services.thumbnail import to_webp


class _BenchmarkEngine(Protocol):
    def generate(self, request): ...


def _engine_factory() -> _BenchmarkEngine:
    try:
        from app.services.thumbnail_engine import ThumbnailEngine

        return ThumbnailEngine()
    except ImportError:
        from app.services.mesh_processing import render_thumbnail

        class LegacyEngine:
            def generate(self, request):
                image = render_thumbnail(request.path)
                return SimpleNamespace(
                    image=image,
                    failure_reason=None,
                    strategy=SimpleNamespace(value="legacy"),
                    peak_rss_bytes=None,
                )

        return LegacyEngine()


def _recipe_fingerprint() -> str:
    try:
        from app.services.thumbnail_generations import recipe_fingerprint

        return recipe_fingerprint()
    except ImportError:
        return "legacy-0.13.0"


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value * 1024 if value < 1 << 40 else value


@dataclass(frozen=True)
class Measurement:
    name: str
    input_bytes: int
    cold_median_ms: float
    warm_median_ms: float
    cold_render_calls: int
    warm_render_calls: int
    strategy: str
    output_bytes: int
    peak_rss_bytes: int | None
    error: str | None


def _export(mesh: trimesh.Trimesh, path: Path) -> None:
    path.write_bytes(mesh.export(file_type="stl"))


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


def benchmark_file(
    path: Path,
    *,
    cold_runs: int,
    warm_runs: int,
    engine_factory: Callable[[], _BenchmarkEngine] = _engine_factory,
) -> Measurement:
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    key = (source_hash, _recipe_fingerprint())
    cache: dict[tuple[str, str], bytes | None] = {}
    cold_ms: list[float] = []
    warm_ms: list[float] = []
    strategy = "none"
    peak_rss: int | None = None
    error: str | None = None
    output = b""

    for _ in range(cold_runs):
        started = time.perf_counter()
        result = engine_factory().generate(
            SimpleNamespace(
                path=path,
                file_type=None,
                width=None,
                height=None,
                include_geometry=False,
                reason="benchmark",
                report=None,
                output_format="WEBP",
            )
        )
        if result.image is None:
            error = (
                result.failure_reason.value if result.failure_reason else "no_output"
            )
            output = b""
            # Deterministic failures are durable negative-cache entries too.
            cache[key] = None
        else:
            output = to_webp(result.image)
            cache[key] = output
        cold_ms.append((time.perf_counter() - started) * 1000)
        strategy = result.strategy.value
        measured_rss = result.peak_rss_bytes or _peak_rss_bytes()
        peak_rss = max(filter(None, (peak_rss, measured_rss)), default=None)

    warm_render_calls = 0
    for _ in range(warm_runs):
        started = time.perf_counter()
        if key not in cache:
            warm_render_calls += 1
        warm_ms.append((time.perf_counter() - started) * 1000)

    return Measurement(
        name=path.name,
        input_bytes=path.stat().st_size,
        cold_median_ms=round(statistics.median(cold_ms), 3),
        warm_median_ms=round(statistics.median(warm_ms), 6),
        cold_render_calls=cold_runs,
        warm_render_calls=warm_render_calls,
        strategy=strategy,
        output_bytes=len(output),
        peak_rss_bytes=peak_rss,
        error=error,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--warm-runs", type=int, default=20)
    parser.add_argument("--label", default="working-tree")
    parser.add_argument("--external-model", type=Path)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    if args.cold_runs < 1 or args.warm_runs < 1:
        parser.error("run counts must be positive")

    with tempfile.TemporaryDirectory(prefix="printstash-thumbnail-bench-") as raw:
        corpus = build_corpus(Path(raw), quick=args.quick)
        if args.external_model is not None:
            corpus.append(args.external_model.resolve())
        measurements = [
            benchmark_file(path, cold_runs=args.cold_runs, warm_runs=args.warm_runs)
            for path in corpus
        ]
    payload = {
        "schema_version": 1,
        "label": args.label,
        "recipe_fingerprint": _recipe_fingerprint(),
        "cold_runs": args.cold_runs,
        "warm_runs": args.warm_runs,
        "measurements": [asdict(item) for item in measurements],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

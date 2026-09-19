"""Measure complete mesh preview paths with deterministic output checks."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import statistics
import struct
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
import psutil
from PIL import Image

from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest

DEFAULT_ITERATIONS = 7
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)]


def _latency(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def _binary_stl(path: Path, triangles: np.ndarray) -> None:
    records = np.zeros(
        len(triangles),
        dtype=[
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ],
    )
    records["vertices"] = triangles
    path.write_bytes(bytes(80) + struct.pack("<I", len(triangles)) + records.tobytes())


def _tetrahedra(count: int) -> np.ndarray:
    base = np.array(
        [
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            [[0, 0, 0], [0, 0, 1], [1, 0, 0]],
            [[0, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 0, 0], [0, 0, 1], [0, 1, 0]],
        ],
        dtype=np.float32,
    )
    copies = np.tile(base, (math.ceil(count / 4), 1, 1))[:count]
    copies += np.arange(len(copies), dtype=np.float32)[:, None, None] % 97
    return copies


def _write_3mf(path: Path, *, embedded: bool) -> None:
    model = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <resources>
  <object id="1" type="model"><mesh><vertices>
   <vertex x="0" y="0" z="0"/><vertex x="20" y="0" z="0"/>
   <vertex x="0" y="20" z="0"/><vertex x="0" y="0" z="20"/>
  </vertices><triangles>
   <triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>
   <triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/>
  </triangles></mesh></object>
  <object id="2" type="model"><components>
   <component objectid="1"/><component objectid="1" transform="1 0 0 0 1 0 0 0 1 30 0 0"/>
  </components></object>
 </resources><build><item objectid="2"/></build>
</model>"""
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("3D/3dmodel.model", ZIP_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, model)
        if embedded:
            image = io.BytesIO()
            Image.new("RGBA", (96, 72), (31, 91, 147, 255)).save(image, "PNG")
            preview = zipfile.ZipInfo("Metadata/thumbnail.png", ZIP_TIMESTAMP)
            preview.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(preview, image.getvalue())


def _fixtures(root: Path, *, quick: bool) -> dict[str, Path]:
    small = root / "small-binary.stl"
    _binary_stl(small, _tetrahedra(48))
    dense = root / "dense-binary.stl"
    _binary_stl(dense, _tetrahedra(2_000 if quick else 200_000))
    ascii_stl = root / "ascii.stl"
    ascii_stl.write_text(
        "solid triangle\n facet normal 0 0 1\n  outer loop\n"
        "   vertex 0 0 0\n   vertex 20 0 0\n   vertex 0 20 0\n"
        "  endloop\n endfacet\nendsolid triangle\n"
    )
    multipart = root / "multipart.3mf"
    _write_3mf(multipart, embedded=False)
    embedded = root / "embedded.3mf"
    _write_3mf(embedded, embedded=True)
    return {
        "small_binary_stl": small,
        "dense_binary_stl": dense,
        "ascii_stl": ascii_stl,
        "multipart_3mf": multipart,
        "embedded_3mf": embedded,
    }


def _measure(path: Path, iterations: int) -> tuple[dict, dict]:
    samples: list[float] = []
    correctness: dict[str, object] | None = None
    for _ in range(iterations):
        started = time.perf_counter_ns()
        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path=path,
                include_geometry=True,
                include_thumbnail=True,
                output_format="PNG",
            )
        )
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        if result.image is None:
            raise RuntimeError(
                f"mesh preview failed for {path.name}: {result.failure_reason}"
            )
        with Image.open(io.BytesIO(result.image)) as decoded:
            decoded.load()
            probe = decoded.convert("RGB").resize((32, 24), Image.Resampling.LANCZOS)
        current = {
            "image_sha256": _sha256(result.image),
            "image_bytes": len(result.image),
            "image_mode": decoded.mode,
            "image_size": list(decoded.size),
            "pixel_probe_rgb_32x24": base64.b64encode(probe.tobytes()).decode(),
            "geometry": result.geometry,
            "strategy": result.strategy.value,
            "complete": result.complete,
            "failure_reason": None
            if result.failure_reason is None
            else result.failure_reason.value,
        }
        if correctness is None:
            correctness = current
        elif current != correctness:
            raise RuntimeError(f"mesh preview changed within one run: {path.name}")
    return correctness or {}, {"preview": _latency(samples)}


def run(iterations: int, *, quick: bool = False) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("Iterations must be positive")
    process = psutil.Process()
    with tempfile.TemporaryDirectory(
        prefix="printstash-mesh-preview-benchmark-"
    ) as raw:
        root = Path(raw)
        fixtures = _fixtures(root, quick=quick)
        correctness: dict[str, dict] = {}
        workloads: dict[str, dict] = {}
        cpu_started = time.process_time()
        elapsed_started = time.perf_counter()
        peak_rss = process.memory_info().rss
        for name, path in fixtures.items():
            correctness[name], workloads[name] = _measure(path, iterations)
            peak_rss = max(peak_rss, process.memory_info().rss)
        return {
            "measurement_protocol": "mesh-preview-v1",
            "iterations_per_workload": iterations,
            "total_seconds": time.perf_counter() - elapsed_started,
            "process_cpu_seconds": time.process_time() - cpu_started,
            "process_peak_rss_bytes": peak_rss,
            "preview_latency": {
                "p95_ms": max(item["preview"]["p95_ms"] for item in workloads.values())
            },
            "source_hashes": {
                name: _sha256(path.read_bytes()) for name, path in fixtures.items()
            },
            "correctness_catalog": correctness,
            "workloads": workloads,
        }


def _assert_compatible(reference: dict, report: dict) -> None:
    for key in ("measurement_protocol", "source_hashes"):
        if reference.get(key) != report.get(key):
            raise ValueError(f"mesh-preview benchmark correctness differs: {key}")
    before = reference.get("correctness_catalog")
    after = report.get("correctness_catalog")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("mesh-preview benchmark correctness differs: workloads")
    if before.keys() != after.keys():
        raise ValueError("mesh-preview benchmark correctness differs: workloads")
    for name in before:
        left, right = before[name], after[name]
        for key in ("geometry", "complete", "failure_reason", "image_size"):
            if left.get(key) != right.get(key):
                raise ValueError(
                    f"mesh-preview benchmark correctness differs: {name}.{key}"
                )
        a = np.frombuffer(
            base64.b64decode(left["pixel_probe_rgb_32x24"]), dtype=np.uint8
        )
        b = np.frombuffer(
            base64.b64decode(right["pixel_probe_rgb_32x24"]), dtype=np.uint8
        )
        if a.shape != b.shape or np.abs(a.astype(np.int16) - b).mean() > 24:
            raise ValueError(f"mesh-preview benchmark pixel comparison differs: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--database", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    report = run(args.iterations, quick=args.quick)
    report["database_dialect"] = args.database
    if args.compare:
        reference = json.loads(args.compare.read_text())
        _assert_compatible(reference, report)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

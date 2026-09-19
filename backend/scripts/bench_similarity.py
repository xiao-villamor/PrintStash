"""Measure geometric fingerprinting and verification with correctness checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import psutil
from printstash_core.mesh.similarity.descriptors import describe_surface
from printstash_core.mesh.similarity.fingerprint import fingerprint_mesh
from printstash_core.mesh.similarity.geometry import measure_surface, prepare_surface
from printstash_core.mesh.similarity.verification import verify_meshes

DEFAULT_ITERATIONS = 7


def _percentile(values: list[float], percentile: float) -> float:
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)]


def _latency(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def _tetrahedron() -> tuple[np.ndarray, np.ndarray]:
    return (
        np.array(
            [[0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]],
            dtype=np.float64,
        ),
        np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64),
    )


def _subdivide(
    vertices: np.ndarray, faces: np.ndarray, levels: int
) -> tuple[np.ndarray, np.ndarray]:
    for _ in range(levels):
        triangles = vertices[faces]
        midpoints = (triangles + np.roll(triangles, -1, axis=1)) / 2
        mids = np.arange(len(faces) * 3).reshape((-1, 3)) + len(vertices)
        a, b, c = faces.T
        ab, bc, ca = mids.T
        vertices = np.vstack((vertices, midpoints.reshape((-1, 3))))
        faces = np.vstack(
            (
                np.column_stack((a, ab, ca)),
                np.column_stack((ab, b, bc)),
                np.column_stack((ca, bc, c)),
                mids,
            )
        )
    return vertices, faces


def _source_hash(vertices: np.ndarray, faces: np.ndarray) -> str:
    digest = hashlib.sha256()
    digest.update(np.asarray(vertices, dtype="<f8").tobytes())
    digest.update(np.asarray(faces, dtype="<i8").tobytes())
    return digest.hexdigest()


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_rounded(item) for item in value]
    return value


def _fingerprint(vertices: np.ndarray, faces: np.ndarray) -> dict[str, Any]:
    fingerprint = fingerprint_mesh(vertices, faces)
    surface = prepare_surface(vertices, faces)
    metrics = measure_surface(surface)
    descriptors = describe_surface(
        surface,
        volume=metrics.volume,
        ambiguous_frame=fingerprint.ambiguous_frame,
    )
    return _rounded(
        {
            "algorithm_version": fingerprint.algorithm_version,
            "keys": None if fingerprint.keys is None else asdict(fingerprint.keys),
            "ambiguous_frame": fingerprint.ambiguous_frame,
            "metrics": asdict(fingerprint.metrics),
            "d2": asdict(fingerprint.d2),
            "shape": asdict(descriptors),
        }
    )


def _verification(
    left: tuple[np.ndarray, np.ndarray],
    right: tuple[np.ndarray, np.ndarray],
) -> dict[str, Any]:
    return _rounded(asdict(verify_meshes(*left, *right, sample_points=256)))


def run(iterations: int, *, quick: bool = False) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("Iterations must be positive")
    tetrahedron = _tetrahedron()
    vertices, faces = tetrahedron
    subdivided = _subdivide(vertices.copy(), faces.copy(), 1 if quick else 3)
    inputs = {
        "tetrahedron": tetrahedron,
        "subdivided": subdivided,
    }
    comparisons = {
        "identical": (tetrahedron, tetrahedron),
        "rescaled_mirrored": (
            tetrahedron,
            (vertices * np.array([-2.0, 2.0, 2.0]) + 100, faces),
        ),
        "retessellated": (tetrahedron, subdivided),
        "similar_shape": (
            tetrahedron,
            (vertices * np.array([1.0, 1.0, 1.07]), faces),
        ),
    }
    fingerprint_samples: list[float] = []
    verification_samples: list[float] = []
    fingerprints: dict[str, dict[str, Any]] = {}
    verifications: dict[str, dict[str, Any]] = {}
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    cpu_started = time.process_time()
    elapsed_started = time.perf_counter()
    for _ in range(iterations):
        for name, (mesh_vertices, mesh_faces) in inputs.items():
            started = time.perf_counter_ns()
            current = _fingerprint(mesh_vertices, mesh_faces)
            fingerprint_samples.append((time.perf_counter_ns() - started) / 1_000_000)
            if name in fingerprints and fingerprints[name] != current:
                raise RuntimeError(f"similarity fingerprint changed within run: {name}")
            fingerprints[name] = current
        for name, (left, right) in comparisons.items():
            started = time.perf_counter_ns()
            current = _verification(left, right)
            verification_samples.append((time.perf_counter_ns() - started) / 1_000_000)
            if name in verifications and verifications[name] != current:
                raise RuntimeError(f"similarity decision changed within run: {name}")
            verifications[name] = current
        peak_rss = max(peak_rss, process.memory_info().rss)
    return {
        "measurement_protocol": "geometric-similarity-v1",
        "iterations_per_workload": iterations,
        "total_seconds": time.perf_counter() - elapsed_started,
        "process_cpu_seconds": time.process_time() - cpu_started,
        "process_peak_rss_bytes": peak_rss,
        "fingerprint_latency": _latency(fingerprint_samples),
        "verification_latency": _latency(verification_samples),
        "source_hashes": {name: _source_hash(*mesh) for name, mesh in inputs.items()},
        "correctness_catalog": {
            "fingerprints": fingerprints,
            "verifications": verifications,
        },
    }


def _assert_close(left: Any, right: Any, path: str) -> None:
    if isinstance(left, bool) or isinstance(right, bool):
        if left is not right:
            raise ValueError(f"similarity benchmark correctness differs: {path}")
    elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not math.isclose(float(left), float(right), rel_tol=1e-7, abs_tol=1e-9):
            raise ValueError(f"similarity benchmark correctness differs: {path}")
    elif isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            raise ValueError(f"similarity benchmark correctness differs: {path}")
        for key in left:
            _assert_close(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            raise ValueError(f"similarity benchmark correctness differs: {path}")
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            _assert_close(a, b, f"{path}[{index}]")
    elif left != right:
        raise ValueError(f"similarity benchmark correctness differs: {path}")


def _assert_compatible(reference: dict, report: dict) -> None:
    for key in ("measurement_protocol", "source_hashes"):
        if reference.get(key) != report.get(key):
            raise ValueError(f"similarity benchmark correctness differs: {key}")
    _assert_close(
        reference.get("correctness_catalog"),
        report.get("correctness_catalog"),
        "correctness_catalog",
    )


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
        _assert_compatible(json.loads(args.compare.read_text()), report)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

"""Compare the research Rust kernel with NumPy on complete repository geometry.

Build proximity_probe.rs with `rustc --edition=2021 -C opt-level=3`, then pass
the executable with --native. --verify also measures complete pair verification.
This module is an executable experiment, never a production backend selector.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import tempfile
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    import numpy as np
    from printstash_core.mesh.similarity import verification
    from printstash_core.mesh.similarity.geometry import prepare_surface, sample_surface
    from printstash_core.mesh.similarity.proximity import SurfaceProximity

    from app.modules.media.geometry_analysis import _load
    from tests.paths import TESTDATA_DIR

    mesh = _load(
        TESTDATA_DIR / "benchy/3dbenchy.stl", "stl", triangle_cap=2_000_000
    ).whole_mesh
    surface = prepare_surface(mesh.vertices, mesh.faces)
    points = sample_surface(surface, 5000, 15401) + np.array([0.0, 0, 0.02])
    tick = time.perf_counter()
    proximity = SurfaceProximity(surface)
    python_build = time.perf_counter() - tick
    tick = time.perf_counter()
    distance, closest = proximity.closest(points)
    python_query = time.perf_counter() - tick
    with tempfile.TemporaryDirectory(prefix="printstash-native-probe-") as directory:
        root = Path(directory)

        class NativeProbe:
            def __init__(self, prepared):
                self.triangles = prepared.vertices[prepared.faces].astype("<f8")
                self.timings = {}

            def closest(self, samples):
                source, target = root / "input.bin", root / "output.bin"
                tick = time.perf_counter()
                source.write_bytes(
                    struct.pack("<QQ", len(self.triangles), len(samples))
                    + self.triangles.tobytes()
                    + samples.astype("<f8").tobytes()
                )
                serialization = time.perf_counter() - tick
                tick = time.perf_counter()
                subprocess.run(
                    [str(args.native.resolve()), str(source), str(target)],
                    check=True,
                    timeout=120,
                )
                process_seconds = time.perf_counter() - tick
                raw = target.read_bytes()
                build, query, work = struct.unpack("<ddQ", raw[:24])
                self.timings = {
                    "build_seconds": build,
                    "query_seconds": query,
                    "triangle_tests": work,
                    "serialization_seconds": serialization,
                    "process_seconds": process_seconds,
                }
                values = np.frombuffer(raw[24:], dtype="<f8").reshape(-1, 4)
                return values[:, 0], values[:, 1:]

        native = NativeProbe(surface)
        native_distance, native_closest = native.closest(points)
        np.testing.assert_allclose(native_distance, distance, rtol=1e-9, atol=1e-9)
        np.testing.assert_allclose(native_closest, closest, rtol=1e-9, atol=1e-8)
        report = {
            "triangles": len(surface.faces),
            "query_points": len(points),
            "python": {"build_seconds": python_build, "query_seconds": python_query},
            "rust_prototype": native.timings,
            "max_distance_error": float(np.abs(native_distance - distance).max()),
            "max_coordinate_error": float(np.abs(native_closest - closest).max()),
        }
        if args.verify:
            results = []
            try:
                for name, implementation in (
                    ("python", SurfaceProximity),
                    ("rust_cli_prototype", NativeProbe),
                ):
                    verification.SurfaceProximity = implementation
                    tick = time.perf_counter()
                    proof = verification.verify_meshes(
                        mesh.vertices, mesh.faces, mesh.vertices + 17, mesh.faces
                    )
                    assert proof.exact_equivalence
                    assert proof.evidence_class == "identical_geometry"
                    results.append(
                        {
                            "implementation": name,
                            "seconds": time.perf_counter() - tick,
                            "surface_chamfer": proof.sampled_surface_chamfer,
                            "surface_hausdorff": proof.sampled_surface_hausdorff,
                            "voxel_iou": proof.voxel_iou,
                        }
                    )
            finally:
                verification.SurfaceProximity = SurfaceProximity
            np.testing.assert_allclose(
                [
                    results[0]["surface_chamfer"],
                    results[0]["surface_hausdorff"],
                    results[0]["voxel_iou"],
                ],
                [
                    results[1]["surface_chamfer"],
                    results[1]["surface_hausdorff"],
                    results[1]["voxel_iou"],
                ],
                atol=1e-10,
            )
            report["complete_verification"] = results
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

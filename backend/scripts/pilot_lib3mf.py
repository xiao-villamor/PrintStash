"""Optional, isolated 3MF capability pilot; not an application adapter.

Install Lib3MF into a disposable environment, not the dependency lock:
  uv pip install --python .venv/bin/python lib3mf==2.5.0
  python -m scripts.pilot_lib3mf run --output /tmp/3mf-pilot.json
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

BACKENDS = ("current", "lib3mf-public", "lib3mf-buffer")


def enabled_backends() -> tuple[str, ...]:
    """Current source is unconditional; native probes need their optional package."""
    return BACKENDS if find_spec("lib3mf") is not None else ("current",)


def _proc_rss(field: str) -> int:
    return next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/self/status").read_text().splitlines()
        if line.startswith(field + ":")
    )


def _transform(value: Any, scale: float, np: Any) -> Any:
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :] = np.array(
        [[value.Fields[row][col] for col in range(3)] for row in range(4)],
        dtype=np.float64,
    ).T
    matrix[:3, 3] *= scale
    return matrix


def _arrays(mesh: Any, binding: Any, mode: str, np: Any) -> tuple[Any, Any]:
    if mode == "lib3mf-public":
        # Public bulk calls return a Python list of ctypes structure proxies.
        vertices = np.array(
            [tuple(point.Coordinates) for point in mesh.GetVertices()], dtype=np.float64
        )
        faces = np.array(
            [tuple(face.Indices) for face in mesh.GetTriangleIndices()], dtype=np.int64
        )
        return vertices, faces
    vertices = np.empty((mesh.GetVertexCount(), 3), dtype=np.float32)
    faces = np.empty((mesh.GetTriangleCount(), 3), dtype=np.uint32)
    for array, structure, symbol in (
        (vertices, binding.Position, "lib3mf_meshobject_getvertices"),
        (faces, binding.Triangle, "lib3mf_meshobject_gettriangleindices"),
    ):
        needed = ctypes.c_uint64()
        buffer = (structure * len(array)).from_buffer(array)
        mesh._wrapper.checkError(
            mesh,
            getattr(mesh._wrapper.lib, symbol)(
                mesh._handle, ctypes.c_uint64(len(array)), needed, buffer
            ),
        )
        if needed.value != len(array):
            raise ValueError("native_count_changed")
    # Own normalized arrays; no borrowed native pointer escapes the model lifetime.
    return vertices.astype(np.float64), faces.astype(np.int64)


def _native_scene(
    path: Path, backend: str, max_faces: int, costs: dict, *, strict: bool
):
    import numpy as np
    from lib3mf import Lib3MF as binding
    from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_VERTICES
    from printstash_core.mesh.similarity.components import (
        Assembly,
        Instance,
        MeshResource,
        expand_scene,
    )

    # This entry also exists in the ARM package, whose top-level helper is absent.
    wrapper = binding.Wrapper(str(Path(binding.__file__).with_name("lib3mf")))
    model: Any = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    reader.SetStrictModeActive(strict)
    started = time.perf_counter()
    try:
        reader.ReadFromFile(str(path))
    finally:
        costs["read_ms"] = (time.perf_counter() - started) * 1000
    costs["native_version"] = list(wrapper.GetLibraryVersion())
    costs["warnings"] = [
        list(reader.GetWarning(i)) for i in range(reader.GetWarningCount())
    ]
    scale = {0: 0.001, 1: 1.0, 2: 10.0, 3: 25.4, 4: 304.8, 5: 1000.0}[
        model.GetUnit().value
    ]
    objects = {}
    faces_seen = 0
    vertices_seen = 0

    def visit(obj):
        nonlocal faces_seen, vertices_seen
        key = str(obj.GetResourceID())
        if key in objects:
            return key
        # Reserve identity before following children; expansion owns cycle refusal.
        objects[key] = None
        if obj.IsMeshObject():
            faces_seen += obj.GetTriangleCount()
            vertices_seen += obj.GetVertexCount()
            if (
                faces_seen > max_faces
                or vertices_seen > MAX_ANALYSIS_VERTICES
                or len(objects) > 4096
            ):
                raise ValueError("resource_limit")
            started = time.perf_counter()
            vertices, faces = _arrays(obj, binding, backend, np)
            vertices *= scale
            costs["arrays_ms"] += (time.perf_counter() - started) * 1000
            objects[key] = MeshResource(key, vertices, faces)
        elif obj.IsComponentsObject():
            if obj.GetComponentCount() > 4096 or len(objects) > 4096:
                raise ValueError("scene_resource_limit")
            children = []
            for index in range(obj.GetComponentCount()):
                component = obj.GetComponent(index)
                target = visit(component.GetObjectResource())
                children.append(
                    Instance(target, _transform(component.GetTransform(), scale, np))
                )
            objects[key] = Assembly(key, tuple(children))
        else:
            raise ValueError("unsupported_object")
        return key

    started = time.perf_counter()
    build = []
    iterator = model.GetBuildItems()
    while iterator.MoveNext():
        item = iterator.GetCurrent()
        key = visit(item.GetObjectResource())
        build.append(Instance(key, _transform(item.GetObjectTransform(), scale, np)))
        if len(build) > 4096:
            raise ValueError("scene_resource_limit")
    scene = expand_scene(tuple(objects.values()), tuple(build), max_faces=max_faces)
    costs["scene_ms"] = (time.perf_counter() - started) * 1000 - costs["arrays_ms"]
    return scene


def measure(
    path: Path, backend: str, *, max_faces: int = 2_000_000, strict: bool = False
) -> dict:
    """One cold process reports successful work and refusals with retained costs."""
    costs = {
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "backend": backend,
        "strict": strict,
        "import_ms": None,
        "read_ms": None,
        "arrays_ms": 0.0,
        "scene_ms": 0.0,
        "materialize_ms": 0.0,
    }
    started = time.perf_counter()
    try:
        import numpy as np
        from printstash_core.mesh.similarity.components import compose_scene

        if backend == "current":
            import trimesh  # noqa: F401

            import_module("lxml.etree")

            from app.modules.media import mesh_resources, three_mf_scene
        else:
            from lib3mf import Lib3MF  # noqa: F401
        costs["import_ms"] = (time.perf_counter() - started) * 1000
        if backend == "current":
            # Instrument existing pure seams; leave parsing/output behavior intact.
            originals = []
            for owner, name, phase in (
                (three_mf_scene, "_attribute_columns", "arrays_ms"),
                (three_mf_scene, "expand_scene", "scene_ms"),
                (mesh_resources, "compose_scene", "materialize_ms"),
            ):
                original = getattr(owner, name)
                originals.append((owner, name, original))

                def timed(*args, _original=original, _phase=phase, **kwargs):
                    phase_start = time.perf_counter()
                    try:
                        return _original(*args, **kwargs)
                    finally:
                        costs[_phase] += (time.perf_counter() - phase_start) * 1000
                        if _phase == "scene_ms":
                            costs["scene_rss_bytes"] = _proc_rss("VmRSS")

                setattr(owner, name, timed)
            phase_start = time.perf_counter()
            try:
                prepared = mesh_resources.load_3mf(path, max_faces=max_faces)
                scene = prepared.scene
                vertices, faces = (
                    prepared.whole_mesh.vertices,
                    prepared.whole_mesh.faces,
                )
            finally:
                costs["read_ms"] = (
                    (time.perf_counter() - phase_start) * 1000
                    - costs["arrays_ms"]
                    - costs["scene_ms"]
                    - costs["materialize_ms"]
                )
                for owner, name, original in originals:
                    setattr(owner, name, original)
        else:
            scene = _native_scene(path, backend, max_faces, costs, strict=strict)
            costs["scene_rss_bytes"] = _proc_rss("VmRSS")
            phase_start = time.perf_counter()
            vertices, faces = compose_scene(scene)
            costs["materialize_ms"] = (time.perf_counter() - phase_start) * 1000
        triangle_points = vertices[faces]
        costs.update(
            outcome="ready",
            faces=len(faces),
            vertices=len(vertices),
            resources=len(scene.resources),
            instances=len(scene.instances),
            bbox=np.ptp(vertices, axis=0).tolist(),
            minimum=np.min(vertices, axis=0).tolist(),
            volume=float(
                np.einsum(
                    "ij,ij->",
                    triangle_points[:, 0],
                    np.cross(triangle_points[:, 1], triangle_points[:, 2]),
                )
                / 6
            ),
            unique_array_bytes=sum(
                r.vertices.nbytes + r.faces.nbytes for r in scene.resources
            ),
            materialized_array_bytes=vertices.nbytes + faces.nbytes,
        )
    except Exception as exc:
        costs.update(outcome="refused", error_type=type(exc).__name__, error=str(exc))
    costs["total_ms"] = (time.perf_counter() - started) * 1000
    costs["rusage_peak_rss_bytes"] = resource.getrusage(
        resource.RUSAGE_SELF
    ).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    # Linux ru_maxrss may retain the generator parent's high watermark across
    # posix_spawn. VmHWM belongs to this process's current mm after exec.
    costs["peak_rss_bytes"] = _proc_rss("VmHWM")
    return costs


def check_expectations(observed: dict, expected: dict) -> list[str]:
    """Compare to independent fixture declarations, never to another backend."""
    import math

    def close(actual, expected):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7)
        )

    mismatches = []
    for key, value in expected.items():
        actual = observed.get(key)
        if isinstance(value, list):
            if (
                not isinstance(actual, list)
                or len(actual) != len(value)
                or not all(close(a, b) for a, b in zip(actual, value, strict=True))
            ):
                mismatches.append(key)
        elif isinstance(value, float):
            if not close(actual, value):
                mismatches.append(key)
        elif actual != value:
            mismatches.append(key)
    return mismatches


def run_case(path: Path, backend: str, *, max_faces: int, strict: bool = False) -> dict:
    started = time.perf_counter()
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    command = [
        sys.executable,
        "-m",
        "scripts.pilot_lib3mf",
        "worker",
        str(path),
        "--backend",
        backend,
        "--max-faces",
        str(max_faces),
    ]
    if strict:
        command.append("--strict")
    result: dict[str, Any]
    try:
        completed = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=60, check=False
        )
        if completed.returncode:
            result = {
                "outcome": "worker_failed",
                "returncode": completed.returncode,
                "stderr": completed.stderr[-2000:],
            }
        else:
            try:
                result = json.loads(completed.stdout)
            except json.JSONDecodeError:
                result = {
                    "outcome": "malformed_reply",
                    "stdout": completed.stdout[-2000:],
                }
    except subprocess.TimeoutExpired:
        result = {"outcome": "timeout"}
    result["cold_process_ms"] = (time.perf_counter() - started) * 1000
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    worker = modes.add_parser("worker")
    worker.add_argument("path", type=Path)
    worker.add_argument("--backend", choices=BACKENDS, required=True)
    worker.add_argument("--max-faces", type=int, default=2_000_000)
    worker.add_argument("--strict", action="store_true")
    run = modes.add_parser("run")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--faces", type=int, nargs="*", default=[])
    run.add_argument("--instances", type=int, default=1)
    run.add_argument("--case", action="append")
    run.add_argument("--backend", choices=BACKENDS, action="append")
    run.add_argument("--external-directory", type=Path)
    run.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    available = enabled_backends()
    requested = [args.backend] if args.mode == "worker" else args.backend or available
    missing = [backend for backend in requested if backend not in available]
    if missing:
        parser.error(
            "native backend requested but optional lib3mf package is unavailable: "
            + ", ".join(missing)
        )
    if args.mode == "worker":
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        print(
            json.dumps(
                measure(
                    args.path,
                    args.backend,
                    max_faces=args.max_faces,
                    strict=args.strict,
                )
            )
        )
        return
    if args.repeat < 1 or args.instances < 1:
        parser.error("repeat and instances must be positive")
    from tests.factories.three_mf_pilot import PilotCase, corpus, load_mesh

    cases = [case for case in corpus() if args.case is None or case.name in args.case]
    cases.extend(load_mesh(count, args.instances) for count in args.faces)
    if args.external_directory:
        cases.extend(
            PilotCase(path.stem, path.read_bytes(), {"outcome": "ready"})
            for path in sorted(args.external_directory.glob("*.3mf"))
        )
    evidence = {
        "schema": 1,
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "source_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "cache": "fresh process, warm OS file cache",
            "address_space_limit_bytes": 1024**3,
        },
        "samples": [],
    }
    with tempfile.TemporaryDirectory(prefix="printstash-3mf-pilot-") as directory:
        for repeat in range(args.repeat):
            for case in cases:
                path = Path(directory) / (case.name + ".3mf")
                path.write_bytes(case.payload)
                backends = list(requested)
                if repeat % 2:
                    backends = list(reversed(backends))
                for backend in backends:
                    observed = run_case(
                        path, backend, max_faces=case.max_faces, strict=args.strict
                    )
                    evidence["samples"].append(
                        {
                            "case": case.name,
                            "sha256": hashlib.sha256(case.payload).hexdigest(),
                            "input_bytes": len(case.payload),
                            "max_faces": case.max_faces,
                            "expected": case.expected,
                            "repeat": repeat,
                            "observed": observed,
                            "mismatches": check_expectations(observed, case.expected),
                        }
                    )
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()

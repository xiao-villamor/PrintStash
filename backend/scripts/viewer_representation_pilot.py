"""Isolated indexed-GLB experiment; no application path imports this script.

Use --output-dir in private scratch storage. Reports measure source parsing and
export in fresh admitted workers, not SQL publication or observed user reuse.
Exact flat-normal export is the control; LOD/compression/GPU instancing are absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource as process_resource
import sys
import tempfile
import time
from contextlib import chdir, redirect_stdout
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from printstash_core.mesh.similarity.components import ExpandedScene
    from trimesh import Trimesh


@dataclass(frozen=True)
class SceneExport:
    glb: bytes
    triangle_count: int
    mesh_count: int
    instance_count: int
    bounds_mm: NDArray[np.float64]
    global_origin_mm: NDArray[np.float64]


def canonical_facets(mesh: Trimesh) -> NDArray[np.float64]:
    """Compare oriented cyclic corner tuples, retaining triangle multiplicity."""
    positions = np.asarray(mesh.vertices)[mesh.faces]
    normals = np.asarray(mesh.vertex_normals)[mesh.faces]
    first = np.lexsort(
        (positions[:, :, 2], positions[:, :, 1], positions[:, :, 0]), axis=1
    )[:, 0]
    rotation = (first[:, None] + np.arange(3)) % 3
    attributes = np.concatenate((positions, normals), axis=2)
    rows = np.take_along_axis(attributes, rotation[:, :, None], axis=1).reshape(
        (-1, 18)
    )
    # Pair facets by oriented geometry, not rounded normals which can reorder
    # adjacent facets sharing the first corner. Normals remain in asserted rows.
    ordered_positions = np.take_along_axis(
        positions, rotation[:, :, None], axis=1
    ).reshape((-1, 9))
    return rows[np.lexsort(ordered_positions.T[::-1])]


def read_source(path: Path) -> tuple[ExpandedScene, dict[str, NDArray[np.float64]]]:
    """Canonical geometry admission plus declared STL normals from library parser."""
    from app.modules.media.three_mf_scene import read_scene

    if path.suffix != ".stl":
        return read_scene(path), {}
    from printstash_core.mesh.similarity.components import (
        ExpandedScene,
        Instance,
        MeshResource,
    )
    from trimesh.exchange.stl import load_stl

    from app.modules.media.mesh_loading import load_mesh

    mesh = load_mesh(path, file_type="stl")
    if mesh is None:
        raise ValueError("missing_viewer_geometry")
    with path.open("rb") as stream:
        declared = load_stl(stream)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if not np.array_equal(vertices, declared["vertices"]) or not np.array_equal(
        faces, declared["faces"]
    ):
        raise ValueError("stl_normal_geometry_mismatch")
    normals = np.asarray(declared["face_normals"], dtype=np.float64)
    if normals.shape != (len(faces), 3) or not np.isfinite(normals).all():
        raise ValueError("invalid_declared_stl_normals")
    return ExpandedScene(
        (MeshResource("stl", vertices, faces),), (Instance("stl", np.eye(4)),)
    ), {"stl": normals}


def export_scene(
    scene: ExpandedScene, *, facet_normals: dict[str, NDArray[np.float64]] | None = None
) -> SceneExport:
    """Retain source resources and placements, with owned position/normal indexing."""
    import trimesh
    from trimesh.exchange.gltf import export_glb

    from app.modules.media.mesh_resources import PreparedScene

    admitted = PreparedScene(scene).scene
    resources = {resource.resource_id: resource for resource in admitted.resources}
    local_origins = {}
    referenced_points = {}
    meshes = {}
    minimum = np.full(3, np.inf)
    maximum = np.full(3, -np.inf)
    face_count = 0
    for resource in admitted.resources:
        used = np.unique(resource.faces)
        points = resource.vertices[used]
        referenced_points[resource.resource_id] = points
        origin = points[0].copy()
        local_origins[resource.resource_id] = origin
        local = np.asarray(resource.vertices) - origin
        face_normals = (facet_normals or {}).get(resource.resource_id)
        if face_normals is None:
            # Only sources without declared normals require geometric evaluation.
            raw = trimesh.Trimesh(
                vertices=local, faces=resource.faces.copy(), process=False
            )
            face_normals = np.asarray(raw.face_normals).copy()
            del raw
        if (
            face_normals.shape != (len(resource.faces), 3)
            or not np.isfinite(face_normals).all()
        ):
            raise ValueError("invalid_viewer_normals")
        source_triangles = local[resource.faces]
        positions = source_triangles.reshape((-1, 3))
        # GLB serializes these attributes as f32. Index the actual packed tuples
        # after f64 rebasing instead of retaining a second f64 six-column copy.
        keys = np.empty((len(positions), 6), dtype=np.float32)
        keys[:, :3] = positions
        keys[:, 3:] = np.repeat(face_normals, 3, axis=0)
        if not np.isfinite(keys).all():
            raise ValueError("unrepresentable_viewer_attributes")
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        if not np.isfinite(unique).all():
            raise ValueError("nonfinite_viewer_attributes")
        packed = unique[:, :3]
        if not np.isfinite(packed).all():
            raise ValueError("unrepresentable_viewer_positions")
        decoded_triangles = packed[inverse.reshape((-1, 3))].astype(np.float64)
        source_edges = source_triangles[:, 1:] - source_triangles[:, :1]
        packed_edges = decoded_triangles[:, 1:] - decoded_triangles[:, :1]
        source_area = np.any(
            np.cross(source_edges[:, 0], source_edges[:, 1]) != 0, axis=1
        )
        packed_area = np.any(
            np.cross(packed_edges[:, 0], packed_edges[:, 1]) != 0, axis=1
        )
        if np.any(source_area & ~packed_area):
            raise ValueError("collapsed_viewer_facets")
        mesh = trimesh.Trimesh(
            vertices=unique[:, :3],
            faces=inverse.reshape((-1, 3)),
            vertex_normals=unique[:, 3:],
            process=False,
        )
        meshes[resource.resource_id] = mesh
    for instance in admitted.instances:
        resource = resources[instance.resource_id]
        points = referenced_points[instance.resource_id]
        placed = points @ instance.transform[:3, :3].T + instance.transform[:3, 3]
        if not np.isfinite(placed).all():
            raise ValueError("nonfinite_viewer_placement")
        minimum = np.minimum(minimum, placed.min(axis=0))
        maximum = np.maximum(maximum, placed.max(axis=0))
        face_count += len(resource.faces)
    # Ordinary controls stay in source-mm world space. High coordinates are
    # explicitly rebased and reported separately from refused canonical STL.
    global_origin = (
        minimum.copy() if np.max(np.abs([minimum, maximum])) > 1e8 else np.zeros(3)
    )
    target = trimesh.Scene()
    for name, mesh in meshes.items():
        target.geometry[name] = mesh
    for index, instance in enumerate(admitted.instances):
        transform = instance.transform.copy()
        transform[:3, 3] += (
            transform[:3, :3] @ local_origins[instance.resource_id] - global_origin
        )
        if not np.isfinite(transform).all():
            raise ValueError("nonfinite_viewer_transform")
        target.graph.update(
            frame_to=f"placement-{index}",
            frame_from=target.graph.base_frame,
            matrix=transform,
            geometry=instance.resource_id,
        )
    encoded = export_glb(target, include_normals=True, unitize_normals=False)
    return SceneExport(
        encoded,
        face_count,
        len(meshes),
        len(admitted.instances),
        np.vstack((minimum, maximum)),
        global_origin,
    )


def measure_case(source: Path, directory: Path) -> dict[str, object]:
    from printstash_core.files import sha256_file

    from app.modules.media.mesh_isolation import MeshWorkerError
    from app.modules.media.stl_worker import convert

    directory.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(source)
    started = time.perf_counter_ns()
    scene, declared_normals = read_source(source)
    parsed = time.perf_counter_ns()
    exported = export_scene(scene, facet_normals=declared_normals)
    prepared = time.perf_counter_ns()
    candidate_path = directory / "candidate.glb"
    with candidate_path.open("xb") as output:
        output.write(exported.glb)
        output.flush()
        os.fsync(output.fileno())
    written = time.perf_counter_ns()
    reference_path = directory / "reference.stl"
    reference_reason = None
    try:
        if source.suffix == ".stl":
            reference_path = source
        else:
            convert(source, "3mf", reference_path)
    except MeshWorkerError as exc:
        reference_reason = exc.reason.value
    finished = time.perf_counter_ns()
    if sha256_file(source) != digest:
        raise ValueError("source_changed_by_pilot")
    source_matrix = np.eye(4)
    source_matrix[:3, 3] = exported.global_origin_mm
    return {
        "source_sha256": digest,
        "reference_path": str(reference_path.absolute())
        if reference_reason is None
        else None,
        "reference_reason": reference_reason,
        "reference_sha256": sha256_file(reference_path)
        if reference_reason is None
        else None,
        "candidate_path": str(candidate_path.absolute()),
        "candidate_sha256": hashlib.sha256(exported.glb).hexdigest(),
        "candidate_bytes": len(exported.glb),
        "reference_bytes": reference_path.stat().st_size
        if reference_reason is None
        else None,
        "triangle_count": exported.triangle_count,
        "bounds_mm": exported.bounds_mm.tolist(),
        "global_origin_mm": exported.global_origin_mm.tolist(),
        "candidate_to_source": source_matrix.reshape(-1).tolist(),
        "mesh_count": exported.mesh_count,
        "instance_count": exported.instance_count,
        "parse_ms": (parsed - started) / 1e6,
        "export_ms": (prepared - parsed) / 1e6,
        "write_fsync_ms": (written - prepared) / 1e6,
        "reference_ms": (finished - written) / 1e6,
        "duration_ms": (finished - started) / 1e6,
        "peak_rss_bytes": process_resource.getrusage(
            process_resource.RUSAGE_SELF
        ).ru_maxrss
        * 1024,
        "normal_policy": "indexed_position_and_flat_facet_normal",
        "instancing_policy": "shared_mesh_placement_nodes_not_gpu_instancing",
    }


def _worker(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    from app.modules.media.mesh_isolation import read_spec

    spec = read_spec(argv)
    result = measure_case(Path(spec["source"]), Path(spec["directory"]))
    payload = json.dumps(result, allow_nan=False).encode()
    if len(payload) > 16384:
        raise ValueError("viewer_pilot_reply_limit")
    output.write(payload)
    output.close()
    return 0


def run(output: Path, selected: tuple[str, ...], repeat: int) -> dict[str, object]:
    from app.modules.media.mesh_isolation import MeshWorkerError, run_worker
    from app.modules.media.native_budget import GeometryWork, MeshSource
    from scripts.benchmark_environment import collect_environment
    from scripts.viewer_representation_corpus import write_sources

    if isinstance(repeat, bool) or not 1 <= repeat <= 100:
        raise ValueError("viewer_repeat_out_of_range")
    output.mkdir(parents=True, exist_ok=True)
    sources = write_sources(output / "sources", selected)
    cases, controls, failures = [], [], []
    for name, source in sources.items():
        samples = []
        for index in range(repeat):
            started = time.perf_counter_ns()
            try:
                payload = run_worker(
                    "scripts.viewer_representation_pilot",
                    {
                        "source": str(source.absolute()),
                        "directory": str((output / name / str(index)).absolute()),
                    },
                    sources=(MeshSource(source, source.suffix[1:]),),
                    work=GeometryWork(),
                )
                if len(payload) > 16384:
                    raise ValueError("viewer_pilot_reply_limit")
                result = cast(dict[str, object], json.loads(payload))
                result["worker_total_ms"] = (time.perf_counter_ns() - started) / 1e6
                samples.append(result)
            except (MeshWorkerError, ValueError, OSError) as exc:
                reason = (
                    exc.reason.value
                    if isinstance(exc, MeshWorkerError)
                    else "invalid_worker_reply"
                    if isinstance(exc, ValueError)
                    else "pilot_io_failed"
                )
                failures.append({"case_id": name, "sample": index, "reason": reason})
                break
        if not samples:
            continue
        row = {
            "case_id": name,
            **samples[0],
            "samples": samples,
            "origin": "testdata/benchy/3dbenchy.stl"
            if name == "real-benchy"
            else "generated:mesh-corpus-v2 visual controls",
            "license": "public-domain" if name == "real-benchy" else "AGPL-3.0",
            "source_format": source.suffix[1:],
            "attribution": "backend/tests/fixtures/search/printed-benchy.md"
            if name == "real-benchy"
            else None,
        }
        (cases if row["reference_reason"] is None else controls).append(row)
    report = {
        "schema_version": 1,
        "scope": "isolated_export_not_SQL_or_browser_adoption",
        "cases": cases,
        "unsupported_controls": controls,
        "failures": failures,
        "versions": {
            name: version(name)
            for name in ("numpy", "trimesh", "scipy", "Pillow", "lxml")
        },
        "environment": asdict(collect_environment()),
        "rss_scope": "combined_parse_candidate_export_and_reference_conversion_worker",
    }
    (output / "manifest.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1].startswith("{"):
        return _worker(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        parser.error("repeat must be between 1 and 100")
    output = args.output_dir.resolve()
    from scripts.bench_mesh_pipeline import (
        configure_private_vault,
        export_private_settings,
    )

    with tempfile.TemporaryDirectory(prefix="viewer-pilot-vault-") as temporary:
        configure_private_vault(Path(temporary))
        with chdir(temporary), redirect_stdout(sys.stderr):
            from app.core.config import settings

            export_private_settings(settings)
            result = run(output, tuple(args.case), args.repeat)
    print(json.dumps(result, allow_nan=False))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

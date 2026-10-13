"""The optional indexed viewer export retains reference facets and placement identity."""

from __future__ import annotations

import io
import json
import struct
import subprocess
import sys

import numpy as np
import pytest
import trimesh

from scripts.mesh_corpus_v2_scenes import SceneCase, scene
from scripts.viewer_representation_corpus import source_builders
from tests.paths import BACKEND_DIR


def _expand_declared_attributes(scene: trimesh.Scene) -> trimesh.Trimesh:
    """Expand loaded GLB attributes without Scene.to_mesh's cache-clearing copies."""
    placed_positions, placed_normals = [], []
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        geometry = scene.geometry[name]
        faces = np.asarray(geometry.faces)
        if np.linalg.det(transform[:3, :3]) < 0:
            faces = faces[:, ::-1]
        positions = np.asarray(geometry.vertices)[faces]
        normals = np.asarray(geometry.vertex_normals)[faces]
        placed_positions.append(positions @ transform[:3, :3].T + transform[:3, 3])
        # Preserve the encoded normal magnitude; the shader receives these values.
        placed_normals.append(normals @ np.linalg.inv(transform[:3, :3]))
    positions = np.concatenate(placed_positions).reshape((-1, 3))
    normals = np.concatenate(placed_normals).reshape((-1, 3))
    return trimesh.Trimesh(
        vertices=positions,
        faces=np.arange(len(positions)).reshape((-1, 3)),
        vertex_normals=normals,
        process=False,
    )


class TestViewerRepresentation:
    @pytest.mark.parametrize(
        "case",
        [
            "sharp-cube",
            "hole-torus",
            "open-cube",
            "thin-solid",
            "remote-component",
            "reversed-winding",
            "nested-transforms",
            "reflection",
        ],
    )
    def test_preserves_reference_facets_in_indexed_glb(self, tmp_path, case):
        from app.modules.media.stl_worker import convert
        from app.modules.media.three_mf_scene import read_scene
        from scripts.viewer_representation_pilot import export_scene

        source = tmp_path / "source.3mf"
        original = source_builders()[case]()
        source.write_bytes(original)
        admitted = read_scene(source)
        before = [
            (resource.vertices.copy(), resource.faces.copy())
            for resource in admitted.resources
        ]
        candidate = export_scene(admitted)
        reference = tmp_path / "reference.stl"
        convert(source, "3mf", reference)
        loaded = trimesh.load(
            io.BytesIO(candidate.glb), file_type="glb", process=False
        ).to_mesh()
        expected = trimesh.load(reference, file_type="stl", process=False)
        actual_triangles = loaded.vertices[loaded.faces]
        expected_triangles = expected.vertices[expected.faces]
        assert len(actual_triangles) == len(expected_triangles)
        # Canonicalize oriented corner tuples, preserving winding and duplicates.
        from scripts.viewer_representation_pilot import canonical_facets

        np.testing.assert_allclose(
            canonical_facets(loaded), canonical_facets(expected), rtol=1e-6, atol=1e-5
        )
        np.testing.assert_allclose(loaded.bounds, expected.bounds, rtol=1e-6, atol=1e-5)
        assert source.read_bytes() == original
        for resource, (vertices, faces) in zip(admitted.resources, before, strict=True):
            np.testing.assert_array_equal(resource.vertices, vertices)
            np.testing.assert_array_equal(resource.faces, faces)

    def test_preserves_sheared_placement_matrix(self, tmp_path):
        from app.modules.media.stl_worker import convert
        from app.modules.media.three_mf_scene import read_scene
        from scripts.mesh_corpus_v2_geometry import box
        from scripts.viewer_representation_pilot import export_scene

        original = source_builders()["sheared-placement"]()
        source = tmp_path / "sheared.3mf"
        source.write_bytes(original)
        affine = np.eye(4)
        affine[0, 1] = 0.5
        expected = np.asarray(box().vertices) @ affine[:3, :3].T
        admitted = read_scene(source)
        np.testing.assert_array_equal(admitted.instances[0].transform, affine)
        before = admitted.resources[0].vertices.copy()
        reference = tmp_path / "reference.stl"
        convert(source, "3mf", reference)
        baseline = trimesh.load(reference, file_type="stl", process=False)
        np.testing.assert_array_equal(
            np.unique(baseline.vertices, axis=0), np.unique(expected, axis=0)
        )
        candidate = export_scene(admitted)
        np.testing.assert_array_equal(candidate.bounds_mm, [[0, 0, 0], [30, 20, 20]])
        assert candidate.triangle_count == 12
        length, kind = struct.unpack_from("<II", candidate.glb, 12)
        assert kind == 0x4E4F534A
        document = json.loads(candidate.glb[20 : 20 + length])
        placements = [node for node in document["nodes"] if "mesh" in node]
        assert len(placements) == 1
        # Inspect serialized column-major matrix directly; Scene.to_mesh could
        # share a loader decomposition bug with the browser and is no oracle.
        np.testing.assert_array_equal(
            np.asarray(placements[0]["matrix"]).reshape((4, 4), order="F"), affine
        )
        np.testing.assert_array_equal(admitted.resources[0].vertices, before)
        assert source.read_bytes() == original

    def test_refuses_float32_collapsed_facets(self):
        from printstash_core.mesh.similarity.components import (
            ExpandedScene,
            Instance,
            MeshResource,
        )

        from scripts.viewer_representation_pilot import export_scene

        vertices = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [1e8, 0.0, 0.0],
                [1e8 + 1.0, 0.0, 0.0],
                [1e8, 1.0, 0.0],
            ]
        )
        faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
        before = vertices.copy()
        admitted = ExpandedScene(
            (MeshResource("control", vertices, faces),),
            (Instance("control", np.eye(4)),),
        )
        with pytest.raises(ValueError, match="^collapsed_viewer_facets$"):
            export_scene(admitted)
        np.testing.assert_array_equal(vertices, before)

    def test_retains_resource_sharing_across_placements(self, tmp_path):
        from app.modules.media.three_mf_scene import read_scene
        from scripts.viewer_representation_pilot import export_scene

        source = tmp_path / "instances.3mf"
        source.write_bytes(scene(SceneCase.INSTANCES))
        admitted = read_scene(source)
        result = export_scene(admitted)
        loaded = trimesh.load(io.BytesIO(result.glb), file_type="glb", process=False)
        assert len(loaded.geometry) == 1
        assert len(loaded.graph.nodes_geometry) == 2048
        assert result.triangle_count == 12 * 2048
        assert result.mesh_count == 1
        assert result.instance_count == 2048
        np.testing.assert_allclose(
            result.bounds_mm, [[0, 0, 0], [2047 * 40 + 20, 20, 20]]
        )

    def test_rebases_a_high_translation_control_explicitly(self, tmp_path):
        from app.modules.media.mesh_contracts import ThumbnailFailureReason
        from app.modules.media.mesh_isolation import MeshWorkerError
        from app.modules.media.stl_worker import convert
        from app.modules.media.three_mf_scene import read_scene
        from scripts.viewer_representation_pilot import export_scene

        source = tmp_path / "translated.3mf"
        source.write_bytes(scene(SceneCase.SOURCE_TRANSLATION))
        with pytest.raises(MeshWorkerError) as failure:
            convert(source, "3mf", tmp_path / "refused.stl")
        assert failure.value.reason is ThumbnailFailureReason.INVALID_SOURCE
        result = export_scene(read_scene(source))
        loaded = trimesh.load(
            io.BytesIO(result.glb), file_type="glb", process=False
        ).to_mesh()
        assert np.isfinite(loaded.vertices).all()
        np.testing.assert_allclose(loaded.extents, [20, 20, 20], rtol=1e-6, atol=1e-5)
        np.testing.assert_allclose(result.global_origin_mm, [1e12, 1e12, 1e12])
        np.testing.assert_allclose(
            loaded.bounds + result.global_origin_mm, result.bounds_mm
        )


class TestCLI:
    def test_retains_a_failed_cell_report(self, tmp_path):
        output = tmp_path / "pilot"
        occupied = output / "sharp-cube" / "0" / "candidate.glb"
        occupied.parent.mkdir(parents=True)
        occupied.write_bytes(b"foreign-output")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.viewer_representation_pilot",
                "--output-dir",
                str(output),
                "--repeat",
                "1",
                "--case",
                "sharp-cube",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        assert completed.returncode == 1
        report = json.loads(completed.stdout)
        assert report == json.loads((output / "manifest.json").read_text())
        assert report["cases"] == []
        assert report["failures"] == [
            {"case_id": "sharp-cube", "sample": 0, "reason": "worker_failed"}
        ]
        assert occupied.read_bytes() == b"foreign-output"
        json.dumps(report, allow_nan=False)

    @pytest.mark.parametrize("repeat", [0, 101])
    def test_refuses_unbounded_trial_counts(self, tmp_path, repeat):
        output = tmp_path / "pilot"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.viewer_representation_pilot",
                "--output-dir",
                str(output),
                "--repeat",
                str(repeat),
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 2
        assert "repeat must be between 1 and 100" in completed.stderr
        assert not output.exists()


class TestRealSTL:
    def test_preserves_original_stl_in_viewer_export(self, tmp_path):
        from scripts.viewer_representation_pilot import measure_case

        source = tmp_path / "benchy.stl"
        original = source_builders()["real-benchy"]()
        source.write_bytes(original)
        result = measure_case(source, tmp_path / "export")
        assert result["reference_path"] == str(source.absolute())
        assert result["source_sha256"] == result["reference_sha256"]
        assert result["reference_bytes"] == len(original)
        assert result["triangle_count"] == 225706
        assert source.read_bytes() == original
        candidate_scene = trimesh.load(
            result["candidate_path"], file_type="glb", process=False
        )
        candidate = _expand_declared_attributes(candidate_scene)
        from trimesh.exchange.stl import load_stl

        with source.open("rb") as stream:
            original_arrays = load_stl(stream)
        expected = trimesh.Trimesh(
            vertices=original_arrays["vertices"],
            faces=original_arrays["faces"],
            vertex_normals=np.repeat(original_arrays["face_normals"], 3, axis=0),
            process=False,
        )
        from scripts.viewer_representation_pilot import canonical_facets

        np.testing.assert_allclose(
            canonical_facets(candidate),
            canonical_facets(expected),
            rtol=1e-6,
            atol=1e-5,
        )

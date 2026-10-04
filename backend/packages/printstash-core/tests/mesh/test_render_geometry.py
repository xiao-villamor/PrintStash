"""Render preparation preserves relative coordinates without flattening scene faces.

Output chunk order must reproduce materialized geometry across placement edges.
Limits are applied before render buffers, and source arrays remain unchanged.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from printstash_core.mesh.render_geometry import (
    prepare_mesh_render,
    prepare_scene,
    prepare_scene_render,
)
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.components import (
    ExpandedScene,
    Instance,
    MeshResource,
)


class TestPrepareScene:
    @pytest.mark.parametrize("size", [1, 5, 20])
    def test_yields_repeatable_chunks_in_placement_order(self, size):
        vertices = np.array([[0.0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]])
        faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
        reflected = np.diag([-2.0, 2, 2, 1])
        reflected[:3, 3] = [100, 0, 0]
        scene = ExpandedScene(
            (MeshResource("part", vertices, faces),),
            (Instance("part", np.eye(4)), Instance("part", reflected)),
        )
        expected_points = np.concatenate(
            (vertices, vertices * [-2, 2, 2] + [100, 0, 0])
        )
        expected_relative = (expected_points - [50, 20, 30]).astype(np.float32)
        expected_faces = np.concatenate((faces, faces[:, ::-1] + 4))
        originals = vertices.tobytes(), faces.tobytes(), reflected.tobytes()

        prepared = prepare_scene(scene)
        first = tuple(prepared.face_chunks(size))
        repeated = tuple(prepared.face_chunks(size))

        np.testing.assert_array_equal(prepared.vertices, expected_relative)
        assert prepared.vertices.dtype == np.float32
        assert prepared.face_count == 8
        assert all(0 < len(chunk) <= size for chunk in first)
        np.testing.assert_array_equal(np.concatenate(first), expected_faces)
        np.testing.assert_array_equal(np.concatenate(repeated), expected_faces)
        assert (vertices.tobytes(), faces.tobytes(), reflected.tobytes()) == originals

    @pytest.mark.parametrize("size", [0, -1, True, 1.0])
    def test_rejects_invalid_chunk_size(self, size):
        scene = ExpandedScene(
            (MeshResource("part", np.eye(3), np.array([[0, 1, 2]])),),
            (Instance("part", np.eye(4)),),
        )
        prepared = prepare_scene(scene)

        with pytest.raises(ValueError, match="invalid_render_chunk"):
            tuple(prepared.face_chunks(size))

    @pytest.mark.parametrize(
        "vertex_count,face_count,instances",
        [(3, 1_000_001, 2), (3_000_001, 1, 2), (3, 1, 2049)],
    )
    def test_rejects_source_limits_before_render_allocation(
        self, monkeypatch, vertex_count, face_count, instances
    ):
        scene = ExpandedScene(
            (
                MeshResource(
                    "part",
                    np.broadcast_to(np.array([[0.0, 0, 0]]), (vertex_count, 3)),
                    np.broadcast_to(np.array([[0, 1, 2]]), (face_count, 3)),
                ),
            ),
            (Instance("part", np.eye(4)),) * instances,
        )

        def allocate(*_args, **_kwargs):
            raise AssertionError("refused scene allocated render output")

        monkeypatch.setattr(np, "empty", allocate)
        with pytest.raises(GeometryError, match="scene_resource_limit"):
            prepare_scene(scene)

    def test_rejects_relative_render_numeric_overflow(self):
        scene = ExpandedScene(
            (MeshResource("part", np.eye(3), np.array([[0, 1, 2]])),),
            (Instance("part", np.diag([1e300, 1e300, 1e300, 1])),),
        )

        with pytest.raises(GeometryError, match="numeric_range"):
            prepare_scene(scene)


@pytest.fixture
def source_mesh():
    return SimpleNamespace(
        vertices=np.array([[0.0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]]),
        faces=np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64),
    )


class TestPrepareMeshRender:
    def test_snapshots_source_geometry(self, source_mesh):
        prepared = prepare_mesh_render(source_mesh, face_chunk_size=2)
        assert prepared is not None
        positions = prepared.vertices.copy()
        indices = np.concatenate(tuple(prepared.face_chunks(2)))
        source_mesh.vertices[:] += [100, 200, 300]
        source_mesh.faces[:] = source_mesh.faces[:, ::-1]
        np.testing.assert_array_equal(prepared.vertices, positions)
        np.testing.assert_array_equal(
            np.concatenate(tuple(prepared.face_chunks(3))), indices
        )

    @pytest.mark.parametrize(
        "field",
        ["vertices", "position_ids", "smooth_normals"],
        ids=["positions", "weld-ids", "normals"],
    )
    def test_rejects_cached_array_mutation(self, source_mesh, field):
        prepared = prepare_mesh_render(source_mesh)
        assert prepared is not None
        cached = getattr(prepared, field)
        assert cached.flags.writeable is False
        with pytest.raises(ValueError):
            cached.flat[0] = 0

    def test_retains_source_buffers_unchanged(self, source_mesh):
        original = source_mesh.vertices.tobytes(), source_mesh.faces.tobytes()
        prepared = prepare_mesh_render(source_mesh)
        assert prepared is not None
        assert prepared.face_count == 4
        assert prepared.vertices.dtype == np.float32
        assert prepared.position_ids.dtype == np.int64
        assert prepared.smooth_normals.dtype == np.float64
        assert (source_mesh.vertices.tobytes(), source_mesh.faces.tobytes()) == original

    @pytest.mark.parametrize(
        "size", [0, -1, True, 1.0], ids=["zero", "negative", "boolean", "float"]
    )
    def test_rejects_invalid_normal_preparation_chunk(self, source_mesh, size):
        with pytest.raises(ValueError, match="invalid_render_chunk"):
            prepare_mesh_render(source_mesh, face_chunk_size=size)

    def test_returns_no_preparation_for_empty_source(self):
        assert prepare_mesh_render(None) is None


class TestPrepareSceneRender:
    def test_snapshots_unique_resources_for_repeated_placements(self, source_mesh):
        transforms = np.tile(np.eye(4), (64, 1, 1))
        transforms[1::2, 0, 0] = -2
        transforms[:, 0, 3] = np.arange(64) * 50
        scene = ExpandedScene(
            (MeshResource("part", source_mesh.vertices, source_mesh.faces),),
            tuple(Instance("part", transform) for transform in transforms),
        )
        original = (
            source_mesh.vertices.tobytes(),
            source_mesh.faces.tobytes(),
            transforms.tobytes(),
        )
        prepared = prepare_scene_render(scene, face_chunk_size=5)
        positions = prepared.vertices.copy()
        indices = np.concatenate(tuple(prepared.face_chunks(5)))
        assert prepared.face_count == 256
        assert (
            source_mesh.vertices.tobytes(),
            source_mesh.faces.tobytes(),
            transforms.tobytes(),
        ) == original
        source_mesh.vertices[:] += [100, 200, 300]
        source_mesh.faces[:] = source_mesh.faces[:, ::-1]
        transforms[:] = np.eye(4)
        np.testing.assert_array_equal(prepared.vertices, positions)
        np.testing.assert_array_equal(
            np.concatenate(tuple(prepared.face_chunks(7))), indices
        )

    def test_preserves_prepared_indices_after_chunk_mutation(self, source_mesh):
        scene = ExpandedScene(
            (MeshResource("part", source_mesh.vertices, source_mesh.faces),),
            (Instance("part", np.eye(4)),) * 2,
        )
        prepared = prepare_scene_render(scene)
        first = tuple(prepared.face_chunks(5))
        expected = np.concatenate(first).copy()
        assert first[0].flags.writeable is False
        with pytest.raises(ValueError):
            first[0][0, 0] = 0
        np.testing.assert_array_equal(
            np.concatenate(tuple(prepared.face_chunks(3))), expected
        )

    def test_refuses_scene_budget_before_cached_preparation(
        self, source_mesh, monkeypatch
    ):
        scene = ExpandedScene(
            (MeshResource("part", source_mesh.vertices, source_mesh.faces),),
            (Instance("part", np.eye(4)),) * 2049,
        )

        def allocate(*args, **kwargs):
            raise AssertionError("refused scene allocated cached render buffers")

        monkeypatch.setattr(np, "empty", allocate)
        with pytest.raises(GeometryError, match="scene_resource_limit"):
            prepare_scene_render(scene)

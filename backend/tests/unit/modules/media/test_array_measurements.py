"""Direct source measurements preserve canonical topology and signed precision."""

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.measurements import VolumeMeasured

from app.modules.media.array_measurements import _component_roots, geometry_from_arrays
from app.modules.media.mesh_measurements import geometry_from_mesh
from app.modules.media.visual_loading import VisualMesh


class TestGeometryFromArrays:
    @pytest.mark.parametrize(
        "kind", ["closed", "open", "inverted", "inconsistent", "cavity", "nonmanifold"]
    )
    @pytest.mark.parametrize(
        "independent", [False, True], ids=["indexed", "stl-facets"]
    )
    def test_preserves_source_measurements(self, kind, independent):
        mesh = trimesh.creation.box(extents=[10, 25, 50])
        if kind == "open":
            mesh.faces = mesh.faces[:-1]
        elif kind == "inverted":
            mesh.invert()
        elif kind == "inconsistent":
            mesh.faces[0] = mesh.faces[0][::-1]
        elif kind == "nonmanifold":
            mesh.faces = np.vstack([mesh.faces, mesh.faces[:1]])
        elif kind == "cavity":
            cavity = trimesh.creation.box(extents=[2, 3, 4])
            cavity.invert()
            mesh = trimesh.util.concatenate([mesh, cavity])
        if independent:
            mesh.unmerge_vertices()
        vertices, faces = (
            np.asarray(mesh.vertices).copy(),
            np.asarray(mesh.faces).copy(),
        )

        actual = geometry_from_arrays(VisualMesh(vertices, faces))
        expected = geometry_from_mesh(mesh)

        assert actual == expected
        np.testing.assert_array_equal(vertices, mesh.vertices)
        np.testing.assert_array_equal(faces, mesh.faces)

    def test_ignores_unreferenced_vertices(self):
        mesh = trimesh.creation.box(extents=[10, 25, 50])
        vertices = np.vstack([mesh.vertices, [[1e9, 1e9, 1e9]]])

        actual = geometry_from_arrays(VisualMesh(vertices, np.asarray(mesh.faces)))

        assert actual == geometry_from_mesh(mesh)
        assert actual.volume == VolumeMeasured(12500)

    def test_preserves_component_relative_precision(self):
        parts = []
        for offset in [-1e15, 1e15]:
            mesh = trimesh.creation.box(extents=[10, 25, 50])
            mesh.apply_translation([offset, 0, 0])
            parts.append(mesh)
        mesh = trimesh.util.concatenate(parts)

        actual = geometry_from_arrays(
            VisualMesh(np.asarray(mesh.vertices), np.asarray(mesh.faces))
        )

        assert actual == geometry_from_mesh(mesh)
        assert actual.volume == VolumeMeasured(25000)

    def test_reports_nonfinite_integral(self):
        mesh = trimesh.creation.box(extents=[1e150, 1e150, 1e150])
        with np.errstate(over="ignore", invalid="ignore"):
            actual = geometry_from_arrays(
                VisualMesh(np.asarray(mesh.vertices), np.asarray(mesh.faces))
            )
            expected = geometry_from_mesh(mesh)
        assert actual == expected


class TestArrayPreparation:
    @pytest.mark.parametrize("unused", [1, 1024])
    def test_preserves_measurements_across_vertex_storage_layouts(self, unused):
        mesh = trimesh.creation.box(extents=[10, 25, 50])
        vertices = np.vstack([mesh.vertices, np.full((unused, 3), 1e9)])
        original = vertices.copy()
        actual = geometry_from_arrays(VisualMesh(vertices, np.asarray(mesh.faces)))
        assert actual == geometry_from_mesh(mesh)
        np.testing.assert_array_equal(vertices, original)

    def test_weld_keeps_first_source_position_within_tolerance(self):
        mesh = trimesh.creation.box(extents=[10, 25, 50])
        mesh.unmerge_vertices()
        vertices = np.asarray(mesh.vertices).copy()
        # Quantized duplicates retain their first original float64 position.
        vertices[::2, 0] += 1e-10
        source = trimesh.Trimesh(vertices=vertices, faces=mesh.faces, process=False)
        before = vertices.copy()
        actual = geometry_from_arrays(VisualMesh(vertices, np.asarray(mesh.faces)))
        assert actual == geometry_from_mesh(source)
        np.testing.assert_array_equal(vertices, before)


class TestComponentRoots:
    @pytest.mark.parametrize("size", [24, 262145])
    def test_interleaved_components_choose_their_first_face(self, size):
        # Two long chains interleave face indices. A disconnected final face
        # is its own component, and repeated/reversed edges change nothing.
        ids = np.arange(size - 1, dtype=np.int64)
        pairs = np.column_stack([ids[:-2], ids[2:]])
        pairs = np.vstack([pairs[::-1, ::-1], pairs[:3]])
        expected = np.arange(size, dtype=np.int64) % 2
        expected[-1] = size - 1
        actual = _component_roots(pairs, size)
        np.testing.assert_array_equal(actual, expected)

    def test_no_edges_leaves_independent_faces(self):
        actual = _component_roots(np.empty((0, 2), dtype=np.int64), 12)
        np.testing.assert_array_equal(actual, np.arange(12))

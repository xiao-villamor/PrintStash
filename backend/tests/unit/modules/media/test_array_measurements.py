"""Direct source measurements preserve canonical topology and signed precision."""

import numpy as np
import pytest
import trimesh
from printstash_core.mesh.measurements import VolumeMeasured

from app.modules.media.array_measurements import geometry_from_arrays
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

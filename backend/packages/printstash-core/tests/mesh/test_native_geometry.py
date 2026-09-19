"""Actual native geometry follows the importer's existing numeric contract."""

import numpy as np
import pytest
import trimesh

from printstash_core.mesh import native_geometry


@pytest.fixture(params=["box", "sphere", "open", "reversed"], ids=str)
def measured_mesh(request):
    shape = request.param
    mesh = (
        trimesh.creation.icosphere(subdivisions=2)
        if shape == "sphere"
        else trimesh.creation.box(extents=[2, 3, 4])
    )
    if shape == "open":
        mesh.update_faces(np.arange(len(mesh.faces) - 1))
    if shape == "reversed":
        mesh.invert()
    expected = dict(
        zip(
            ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"],
            np.round(mesh.extents, 2),
            strict=True,
        )
    )
    expected.update(
        volume_mm3=round(mesh.volume, 2) if mesh.volume > 0 else None,
        triangle_count=len(mesh.faces),
    )
    return mesh, expected


class TestMeasureMesh:
    def test_preserves_import_measurements(self, measured_mesh):
        mesh, expected = measured_mesh

        actual = native_geometry.measure_mesh(mesh, face_chunk_size=3)

        assert actual == expected

    def test_ignores_unreferenced_vertices_in_bounds(self):
        mesh = trimesh.creation.box(extents=[2, 3, 4])
        mesh.vertices = np.vstack([mesh.vertices, [999, 999, 999]])

        actual = native_geometry.measure_mesh(mesh)

        assert [actual[axis] for axis in ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"]] == [
            2,
            3,
            4,
        ]

    def test_bounds_triangle_batches(self, monkeypatch):
        import printstash_mesh_native

        mesh = trimesh.creation.icosphere(subdivisions=2)
        original = printstash_mesh_native.measure_triangles
        sizes = []

        def measured(data):
            sizes.append(len(data))
            return original(data)

        monkeypatch.setattr(printstash_mesh_native, "measure_triangles", measured)

        actual = native_geometry.measure_mesh(mesh, face_chunk_size=13)

        assert max(sizes) == 13 * 72
        assert actual["triangle_count"] == len(mesh.faces)

    def test_empty_mesh_has_no_measurements(self):
        actual = native_geometry.measure_mesh(trimesh.Trimesh())

        assert actual == {
            "bbox_x_mm": None,
            "bbox_y_mm": None,
            "bbox_z_mm": None,
            "volume_mm3": None,
            "triangle_count": None,
        }

    def test_rejects_invalid_chunk_size(self):
        with pytest.raises(ValueError, match="chunk size"):
            native_geometry.measure_mesh(trimesh.creation.box(), face_chunk_size=0)

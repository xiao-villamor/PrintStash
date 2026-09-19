"""Native measurements retain the geometry used by existing imports."""

import numpy as np
import printstash_mesh_native as native
import pytest
import trimesh


class TestMeasureTriangles:
    def test_measures_referenced_bounds(self):
        triangles = np.array([[[-3, 4, 2], [7, -2, 8], [0, 1, -9]]], dtype=np.float64)

        bounds, _ = native.measure_triangles(triangles.tobytes())

        np.testing.assert_array_equal(bounds, [[-3, -2, -9], [7, 4, 8]])

    @pytest.mark.parametrize("reverse", [False, True], ids=["outward", "inward"])
    def test_measures_signed_volume(self, reverse):
        mesh = trimesh.creation.box(extents=[2, 3, 4])
        triangles = np.asarray(mesh.triangles[:, ::-1] if reverse else mesh.triangles)

        _, integral = native.measure_triangles(triangles.tobytes())

        assert integral / 6 == pytest.approx(-24 if reverse else 24)

    def test_preserves_open_surface_volume_convention(self):
        triangles = np.array([[[1, 0, 0], [1, 2, 0], [1, 0, 3]]], dtype=np.float64)
        expected = trimesh.triangles.mass_properties(triangles).volume

        _, integral = native.measure_triangles(triangles.tobytes())

        assert integral / 6 == expected

    def test_handles_empty_geometry(self):
        assert native.measure_triangles(b"") == (None, 0.0)

    @pytest.mark.parametrize("size", [1, 71, 73], ids=["one-byte", "short", "trailing"])
    def test_rejects_malformed_triangles(self, size):
        with pytest.raises(ValueError, match="triangle buffer"):
            native.measure_triangles(bytes(size))

    @pytest.mark.parametrize(
        "coordinate",
        [np.nan, np.inf, -np.inf],
        ids=["nan", "positive-inf", "negative-inf"],
    )
    def test_rejects_nonfinite_coordinates(self, coordinate):
        triangles = np.zeros((1, 3, 3), dtype=np.float64)
        triangles[0, 0, 0] = coordinate

        with pytest.raises(ValueError, match="finite"):
            native.measure_triangles(triangles.tobytes())

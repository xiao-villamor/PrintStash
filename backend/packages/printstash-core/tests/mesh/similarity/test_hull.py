"""Hull volume retains physical units and refuses invalid or oversized point sets.

Dense convex surfaces are valid inputs; numeric normalization must not erase
small solids or overflow finite ones before restoring their volume.
"""

import numpy as np
import pytest

from printstash_core.mesh.similarity.hull import hull_volume


class TestHullVolume:
    def test_calculates_convex_hull_of_dense_interior(self, cube):
        interior = np.random.default_rng(154).uniform(-0.9, 0.9, (10_000, 3))
        points = np.vstack((cube[0], interior))

        volume = hull_volume(points, max_points=100_000)

        assert volume == pytest.approx(8)

    def test_preserves_hull_under_vertex_reordering(self, tetra):
        vertices = np.vstack((tetra[0], [[2.0, 3, 4], [1, 1, 1]]))
        shuffled = vertices[np.random.default_rng(154).permutation(len(vertices))]

        volume = hull_volume(shuffled)

        assert volume == pytest.approx(1000)


class TestHullNumerics:
    def test_retains_dense_convex_surface(self):
        points = np.random.default_rng(20261004).normal(size=(20_000, 3))
        points /= np.linalg.norm(points, axis=1)[:, None]

        volume = hull_volume(points)

        assert volume == pytest.approx(4 * np.pi / 3, rel=0.002)

    @pytest.mark.parametrize("scale", [1e-100, 1e-6, 1e30, 1e100])
    def test_preserves_volume_across_finite_scales(self, cube, scale):
        vertices = cube[0] * scale

        volume = hull_volume(vertices)

        assert volume / scale**3 == pytest.approx(8, rel=1e-12)

    @pytest.mark.parametrize(
        "points",
        [
            np.empty((0, 3)),
            np.zeros((1, 3)),
            np.zeros((4, 3)),
            np.column_stack((np.arange(4), np.zeros((4, 2)))),
        ],
        ids=["empty", "point", "duplicates", "collinear"],
    )
    def test_refuses_degenerate_points(self, points):
        from printstash_core.mesh.similarity import GeometryError

        with pytest.raises(GeometryError, match="degenerate_hull"):
            hull_volume(points)

    @pytest.mark.parametrize("coordinate", [np.nan, np.inf, -np.inf])
    def test_refuses_nonfinite_points(self, cube, coordinate):
        from printstash_core.mesh.similarity import GeometryError

        points = cube[0].copy()
        points[0, 0] = coordinate
        with pytest.raises(GeometryError, match="invalid_geometry"):
            hull_volume(points)


class TestHullAdmission:
    @pytest.mark.parametrize("budget", [0, 6_000_001, True, 1.5])
    def test_refuses_invalid_point_budget(self, cube, budget):
        from printstash_core.mesh.similarity import GeometryError

        with pytest.raises(GeometryError, match="invalid_hull_budget"):
            hull_volume(cube[0], max_points=budget)

    def test_refuses_points_before_native_allocation(self, cube, monkeypatch):
        import scipy.spatial

        from printstash_core.mesh.similarity import GeometryError

        def unexpected(*args, **kwargs):
            raise AssertionError("native hull must not receive over-budget points")

        monkeypatch.setattr(scipy.spatial, "ConvexHull", unexpected)
        with pytest.raises(GeometryError, match="hull_resource_limit"):
            hull_volume(cube[0], max_points=7)

    def test_accepts_exact_point_budget(self, cube):
        assert hull_volume(cube[0], max_points=8) == pytest.approx(8)

    @pytest.mark.parametrize(
        "points", [np.zeros(3), np.zeros((4, 2)), np.zeros((4, 3, 1))]
    )
    def test_refuses_invalid_point_shape(self, points):
        from printstash_core.mesh.similarity import GeometryError

        with pytest.raises(GeometryError, match="invalid_geometry"):
            hull_volume(points)

    @pytest.mark.parametrize("scale", [1e-110, 1e110])
    def test_refuses_unrepresentable_volume(self, cube, scale):
        from printstash_core.mesh.similarity import GeometryError

        with pytest.raises(GeometryError, match="invalid_geometry"):
            hull_volume(cube[0] * scale)

    def test_keeps_source_coordinates_unchanged(self, cube):
        points = cube[0].copy()
        expected = points.copy()
        points.flags.writeable = False

        assert hull_volume(points) == pytest.approx(8)
        np.testing.assert_array_equal(points, expected)


class TestHullPrecisionEdges:
    def test_rejects_four_coplanar_points(self):
        from printstash_core.mesh.similarity import GeometryError

        square = np.array(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]]
        )
        with pytest.raises(GeometryError, match="degenerate_hull"):
            hull_volume(square)

    def test_preserves_a_representable_subnormal_volume(self, cube):
        from decimal import Decimal

        expected = float(Decimal(8) * Decimal("1e-108") ** 3)
        assert expected > 0
        assert hull_volume(cube[0] * 1e-108) == expected

    def test_preserves_small_geometry_after_translation(self, cube):
        points = cube[0] * 0.125 + np.array([1e9, -1e9, 1e9])

        assert hull_volume(points) == pytest.approx(0.25**3, rel=1e-12)

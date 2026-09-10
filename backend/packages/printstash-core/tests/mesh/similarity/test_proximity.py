"""Surface distances distinguish face interiors, edges and vertices within bounded work."""

import numpy as np
import pytest

from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.geometry import prepare_surface
from printstash_core.mesh.similarity.proximity import SurfaceProximity


class TestSurfaceProximity:
    def test_projects_onto_triangle_features(self):
        surface = prepare_surface(
            np.array([[0.0, 0, 0], [3, 0, 0], [0, 3, 0]]), np.array([[0, 1, 2]])
        )
        points = np.array([[1.0, 1, 2], [2, 2, 0], [-1, -1, 0]]) - surface.centroid
        distances, closest = SurfaceProximity(surface).closest(points)
        np.testing.assert_allclose(distances, [2, np.sqrt(0.5), np.sqrt(2)])
        np.testing.assert_allclose(
            closest + surface.centroid, [[1, 1, 0], [1.5, 1.5, 0], [0, 0, 0]]
        )

    def test_prunes_subdivided_surfaces(self, tetra, subdivide):
        surface = prepare_surface(*subdivide(*tetra, levels=4))
        points = surface.vertices[:100]
        distances, closest = SurfaceProximity(surface).closest(points)
        np.testing.assert_allclose(distances, 0, atol=1e-12)
        np.testing.assert_allclose(closest, points, atol=1e-12)

    def test_stops_when_work_budget_is_exhausted(self, tetra):
        proximity = SurfaceProximity(prepare_surface(*tetra))
        with pytest.raises(GeometryError, match="proximity_work_limit"):
            proximity.closest(np.zeros((2, 3)), max_work=1)

    @pytest.mark.parametrize(
        "points",
        [
            np.empty((0, 3)),
            np.zeros((5001, 3)),
            np.ones((3, 4)),
            np.full((1, 3), np.nan),
        ],
    )
    def test_rejects_invalid_points(self, tetra, points):
        with pytest.raises(GeometryError, match="invalid_proximity_points"):
            SurfaceProximity(prepare_surface(*tetra)).closest(points)

    @pytest.mark.parametrize("budget", [0, True, 32_000_001])
    def test_rejects_invalid_budget(self, tetra, budget):
        with pytest.raises(GeometryError, match="invalid_proximity_budget"):
            SurfaceProximity(prepare_surface(*tetra)).closest(
                np.zeros((1, 3)), max_work=budget
            )

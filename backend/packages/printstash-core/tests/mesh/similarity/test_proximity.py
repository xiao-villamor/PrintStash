"""Surface distances distinguish face interiors, edges and vertices within bounded work."""

from types import SimpleNamespace

import numpy as np
import pytest

from printstash_core.mesh.native_rasterizer import kernel
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

    @pytest.mark.parametrize("extension", [None, object()], ids=["absent", "older"])
    def test_fails_clearly_without_native_kernel(self, tetra, monkeypatch, extension):
        from printstash_core.mesh import native_rasterizer

        monkeypatch.setattr(native_rasterizer, "kernel", lambda: extension)
        with pytest.raises(GeometryError, match="native_similarity_unavailable"):
            SurfaceProximity(prepare_surface(*tetra))

    def test_aligns_against_the_owned_surface(self, tetra):
        surface = prepare_surface(*tetra)
        proximity = SurfaceProximity(surface)
        diagonal = float(np.linalg.norm(np.ptp(surface.vertices, axis=0)))

        error, rotation, offset, convergence = proximity.align(
            surface.vertices, np.eye(3), diagonal
        )

        assert error == pytest.approx(0, abs=1e-12)
        assert convergence == pytest.approx(0, abs=1e-12)
        np.testing.assert_allclose(rotation, np.eye(3), atol=1e-12)
        np.testing.assert_allclose(offset, 0, atol=1e-12)

    def test_retains_geometry_after_caller_mutation(self, tetra):
        surface = prepare_surface(*tetra)
        points = surface.vertices.copy()
        proximity = SurfaceProximity(surface)
        surface.vertices[:] = 1000
        distances, closest = proximity.closest(points)
        np.testing.assert_allclose(distances, 0, atol=1e-12)
        np.testing.assert_allclose(closest, points, atol=1e-12)

    def test_translates_native_alignment_rejection(self, tetra, monkeypatch):
        from printstash_core.mesh import native_rasterizer

        class RejectingTree:
            def __init__(self, _triangles: bytes) -> None:
                pass

            def align(self, *_args):
                raise ValueError("invalid_alignment")

        monkeypatch.setattr(
            native_rasterizer,
            "kernel",
            lambda: SimpleNamespace(SurfaceTree=RejectingTree),
        )
        proximity = SurfaceProximity(prepare_surface(*tetra))

        with pytest.raises(GeometryError, match="invalid_alignment"):
            proximity.align(np.zeros((1, 3)), np.eye(3), 1.0)


if hasattr(kernel(), "SurfaceTree"):

    class TestNativeSurfaceTree:
        def test_translates_rejected_surface_to_geometry_error(self, tetra):
            surface = prepare_surface(*tetra)
            surface.vertices[:] = np.nan
            with pytest.raises(GeometryError, match="invalid_proximity_surface"):
                SurfaceProximity(surface)

        def test_accepts_the_maximum_query_size(self, tetra):
            surface = prepare_surface(*tetra)
            points = np.repeat(surface.vertices[:1], 5000, axis=0)
            distances, closest = SurfaceProximity(surface).closest(points)
            np.testing.assert_allclose(distances, 0, atol=1e-12)
            np.testing.assert_allclose(closest, points, atol=1e-12)

        def test_is_deterministic_for_arbitrary_queries(self, tetra, subdivide):
            surface = prepare_surface(*subdivide(*tetra, levels=4))
            points = np.random.default_rng(927).uniform(-2, 2, size=(250, 3))
            expected = SurfaceProximity(surface).closest(points)
            actual = SurfaceProximity(surface).closest(points.copy())
            np.testing.assert_array_equal(actual[0], expected[0])
            np.testing.assert_array_equal(actual[1], expected[1])

        @pytest.mark.parametrize(
            "triangles",
            [
                b"",
                b"bad",
                np.zeros((1, 3, 3)).tobytes(),
                np.full((1, 3, 3), np.nan).tobytes(),
                np.full((1, 3, 3), np.inf).tobytes(),
            ],
        )
        def test_rejects_malformed_surface(self, triangles):
            with pytest.raises(ValueError, match="invalid_proximity_surface"):
                kernel().SurfaceTree(triangles)

        @pytest.mark.parametrize(
            "points",
            [
                b"",
                b"bad",
                np.zeros((5001, 3)).tobytes(),
                np.full((1, 3), np.nan).tobytes(),
                np.full((1, 3), np.inf).tobytes(),
            ],
        )
        def test_rejects_malformed_queries(self, tetra, points):
            surface = prepare_surface(*tetra)
            tree = kernel().SurfaceTree(surface.vertices[surface.faces].tobytes())
            with pytest.raises(ValueError, match="invalid_proximity_points"):
                tree.closest(points, 100)

        @pytest.mark.parametrize("budget", [0, 32_000_001])
        def test_rejects_invalid_native_budget(self, tetra, budget):
            surface = prepare_surface(*tetra)
            tree = kernel().SurfaceTree(surface.vertices[surface.faces].tobytes())
            with pytest.raises(ValueError, match="invalid_proximity_budget"):
                tree.closest(np.zeros((1, 3)).tobytes(), budget)

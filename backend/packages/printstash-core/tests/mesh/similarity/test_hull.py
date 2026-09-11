"""Dense interior tessellation must not exhaust a simple solid's hull budget."""

import numpy as np
import pytest

from printstash_core.mesh.similarity.hull import hull_volume


class TestHullVolume:
    def test_calculates_convex_hull_of_dense_interior(self, cube):
        interior = np.random.default_rng(154).uniform(-0.9, 0.9, (10_000, 3))
        points = np.vstack((cube[0], interior))

        volume = hull_volume(points, max_work=100_000)

        assert volume == pytest.approx(8)

    def test_preserves_hull_under_vertex_reordering(self, tetra):
        vertices = np.vstack((tetra[0], [[2.0, 3, 4], [1, 1, 1]]))
        shuffled = vertices[np.random.default_rng(154).permutation(len(vertices))]

        volume = hull_volume(shuffled)

        assert volume == pytest.approx(1000)

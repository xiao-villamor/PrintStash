"""Alignment primitives retain physical origins and enforce bounded sampling.

Exact equivalence requires a positional bijection plus matching triangles; a
nearest-neighbor score or equal vertex count alone cannot establish identity.
"""

from __future__ import annotations

import numpy as np
import pytest

from printstash_core.mesh.similarity.fingerprint import GeometryError
from printstash_core.mesh.similarity.geometry import (
    equivalent_triangles,
    nearest_neighbors,
    prepare_surface,
    sample_surface,
)


class TestPrepareSurface:
    def test_retains_original_origin(self, tetra):
        vertices, faces = tetra

        result = prepare_surface(vertices + [100, -200, 50], faces)

        restored = result.vertices + result.centroid
        np.testing.assert_allclose(
            restored[np.lexsort(restored.T[::-1])],
            (vertices + [100, -200, 50])[np.lexsort(vertices.T[::-1])],
            atol=1e-12,
        )
        assert result.radius > 0
        assert np.linalg.det(result.frame) == pytest.approx(1)

    def test_rejects_numeric_range(self, tetra):
        vertices, faces = tetra

        with pytest.raises(GeometryError, match="numeric_range"):
            prepare_surface(vertices * 1e200, faces)


class TestSampleSurface:
    def test_repeats_seeded_samples(self, tetra):
        surface = prepare_surface(*tetra)
        first = sample_surface(surface, 100, 100)

        second = sample_surface(surface, 100, 100)

        np.testing.assert_array_equal(first, second)
        assert second.shape == (100, 3)

    @pytest.mark.parametrize("count", [0, -1, True, 10001], ids=str)
    def test_rejects_invalid_count(self, tetra, count):
        surface = prepare_surface(*tetra)

        with pytest.raises(GeometryError, match="invalid_sample_count"):
            sample_surface(surface, count, 100)


class TestNearestNeighbors:
    def test_finds_points_across_blocks(self):
        target = np.column_stack((np.arange(300), np.zeros(300), np.zeros(300)))
        source = target[::-1] + [0, 2, 0]

        distance, indices = nearest_neighbors(source, target)

        np.testing.assert_allclose(distance, np.full(300, 2))
        np.testing.assert_array_equal(indices, np.arange(300)[::-1])

    def test_accepts_empty_source(self):
        distances, indices = nearest_neighbors(np.empty((0, 3)), np.zeros((1, 3)))

        assert distances.size == indices.size == 0

    def test_rejects_empty_target(self):
        with pytest.raises(GeometryError, match="empty_target"):
            nearest_neighbors(np.zeros((1, 3)), np.empty((0, 3)))


class TestEquivalentTriangles:
    def test_verifies_permuted_vertices(self, tetra):
        vertices, faces = tetra
        left = prepare_surface(vertices, faces)
        right = prepare_surface(vertices[::-1], 3 - faces[:, ::-1])

        result = equivalent_triangles(left, right, np.eye(3), 1, np.zeros(3), 1e-6)

        assert result is True

    def test_rejects_different_topology(self, tetra):
        vertices, faces = tetra
        left = prepare_surface(vertices, faces)
        right = prepare_surface(vertices, faces[:-1])

        result = equivalent_triangles(left, right, np.eye(3), 1, np.zeros(3), 1e-6)

        assert result is False

    def test_rejects_positional_mismatch(self, tetra):
        vertices, faces = tetra
        left = prepare_surface(vertices, faces)
        right = prepare_surface(vertices * [1, 1, 2], faces)

        result = equivalent_triangles(left, right, np.eye(3), 1, np.zeros(3), 1e-6)

        assert result is False

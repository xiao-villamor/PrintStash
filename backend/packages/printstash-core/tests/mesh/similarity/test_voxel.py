"""Voxel evidence preserves holes and distinguishes surface from solid fill."""

from __future__ import annotations

import numpy as np
import pytest

from printstash_core.mesh.similarity.fingerprint import GeometryError
from printstash_core.mesh.similarity.voxel import voxelize


class TestVoxelize:
    def test_fills_closed_cube(self, cube):
        vertices, faces = cube

        grid = voxelize(vertices, faces, half_width=2)

        assert grid.shape == (64, 64, 64)
        assert grid[32, 32, 32]
        assert not grid[0, 0, 0]
        assert grid.sum() == 32 * 32 * 33

    def test_preserves_hollow_interior(self, cube):
        vertices, faces = cube
        hollow_vertices = np.vstack((vertices, vertices * 0.5))
        hollow_faces = np.vstack((faces, faces[:, ::-1] + 8))

        grid = voxelize(hollow_vertices, hollow_faces, half_width=2)

        assert not grid[32, 32, 32]
        assert grid[18, 32, 32]

    def test_keeps_open_surface_unfilled(self, cube):
        vertices, faces = cube

        grid = voxelize(vertices, faces, half_width=2, fill=False)

        assert not grid[32, 32, 32]
        for axis in range(3):
            for wall in (16, 48):
                point = [32, 32, 32]
                point[axis] = wall
                assert grid[tuple(point)]
        assert 32 * 32 * 5 < grid.sum() <= 32 * 32 * 6

    def test_handles_empty_projection(self):
        grid = voxelize(np.zeros((3, 3)), np.array([[0, 1, 2]]), half_width=1)

        assert not grid.any()

    @pytest.mark.parametrize("width", [0, -1, np.inf, np.nan], ids=str)
    def test_rejects_invalid_cube(self, cube, width):
        with pytest.raises(GeometryError, match="invalid_voxel_recipe"):
            voxelize(*cube, half_width=width)

    def test_rejects_excessive_crossings(self, cube):
        vertices, faces = cube
        repeated_faces = np.tile(faces, (1000, 1))

        with pytest.raises(GeometryError, match="voxel_resource_limit"):
            voxelize(vertices, repeated_faces, half_width=1.01)

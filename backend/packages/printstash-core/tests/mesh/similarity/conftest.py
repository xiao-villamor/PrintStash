"""Analytic geometric surfaces shared by the similarity contracts."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def subdivide():
    def apply(vertices, faces, levels=1):
        for _ in range(levels):
            triangles = vertices[faces]
            midpoints = (triangles + np.roll(triangles, -1, axis=1)) / 2
            mids = np.arange(len(faces) * 3).reshape((-1, 3)) + len(vertices)
            a, b, c = faces.T
            ab, bc, ca = mids.T
            vertices = np.vstack((vertices, midpoints.reshape((-1, 3))))
            faces = np.vstack(
                (
                    np.column_stack((a, ab, ca)),
                    np.column_stack((ab, b, bc)),
                    np.column_stack((ca, bc, c)),
                    mids,
                )
            )
        return vertices, faces

    return apply


@pytest.fixture
def tetra():
    return (
        np.array([[0, 0, 0], [10, 0, 0], [1, 20, 0], [2, 3, 30]], dtype=np.float64),
        np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64),
    )


@pytest.fixture
def cube():
    vertices = np.array(
        [
            [-1.0, -1, -1],
            [1, -1, -1],
            [1, 1, -1],
            [-1, 1, -1],
            [-1, -1, 1],
            [1, -1, 1],
            [1, 1, 1],
            [-1, 1, 1],
        ]
    )
    faces = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ]
    )
    return vertices, faces

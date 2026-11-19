"""Cached rendering of a fixed, small bracket for the appearance controls."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import TYPE_CHECKING

from printstash_core.mesh.preview_profile import (
    PreviewAppearance,
    appearance_scope,
)
from printstash_core.mesh.rasterizer import render_mesh_thumbnail

if TYPE_CHECKING:
    from numpy import float64, int64
    from numpy.typing import NDArray


@dataclass(frozen=True)
class Bracket:
    vertices: NDArray[float64]
    faces: NDArray[int64]


def _bracket() -> Bracket:
    import numpy as np

    x = (-24, -17, -9, 9, 17, 24)
    y = (-16, -9, -1, 10, 16)
    z = (0, 5, 13, 24)
    occupied = {
        (i, j, k)
        for i in range(len(x) - 1)
        for j in range(len(y) - 1)
        for k in range(len(z) - 1)
        if (k == 0 and (i, j) not in {(1, 1), (3, 1)})
        or (j == 3 and k > 0 and (i, k) != (2, 1))
    }
    vertices = []
    faces = []
    for position in sorted(occupied):
        for axis in range(3):
            right, up = (axis + 1) % 3, (axis + 2) % 3
            for side in (0, 1):
                other = list(position)
                other[axis] += 1 if side else -1
                if tuple(other) in occupied:
                    continue
                start = len(vertices)
                for a, b in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    corner = list(position)
                    corner[axis] += side
                    corner[right] += a
                    corner[up] += b
                    vertices.append(
                        tuple(
                            values[index]
                            for values, index in zip((x, y, z), corner, strict=True)
                        )
                    )
                triangles = ((0, 1, 2), (0, 2, 3))
                faces.extend(
                    tuple(start + i for i in (face if side else face[::-1]))
                    for face in triangles
                )
    return Bracket(
        np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)
    )


_sample_lock = Lock()


@lru_cache(maxsize=10)
def _sample(appearance: PreviewAppearance, fingerprint: str) -> bytes:
    with appearance_scope(appearance):
        image = render_mesh_thumbnail(
            _bracket(), "Example bracket", 320, 240, output_format="WEBP"
        )
    if image is None:
        raise RuntimeError("preview_example_unavailable")
    return image


def render_example(appearance: PreviewAppearance) -> bytes:
    # Cache cold requests serially so concurrent readers cannot duplicate native work.
    with _sample_lock:
        return _sample(appearance, appearance.fingerprint)

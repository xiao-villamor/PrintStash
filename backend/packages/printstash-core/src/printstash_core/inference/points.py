"""OpenShape B/32 preprocessing, outside ONNX and bounded to a single cloud.

The paired checkpoint uses 64 FPS centers and radius grouping, not nearest-k
neighbors. Fixing FPS's starting index makes the native vectors reproducible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .embedding import EmbeddingError, EmbeddingInput

if TYPE_CHECKING:
    from numpy.typing import NDArray

POINT_RECIPE = "surface10k-seed166-unitball-yup-rgb04-fps64-start0-radius04-first256-v1"
POINT_COUNT = 10_000


def point_input(vertices: NDArray[Any], faces: NDArray[Any]) -> EmbeddingInput:
    """Sample complete triangle geometry using the shared surface sampler."""
    import numpy as np

    from printstash_core.mesh.similarity.geometry import prepare_surface, sample_surface

    surface = prepare_surface(vertices, faces)
    points = sample_surface(surface, POINT_COUNT, seed=166)
    points -= points.mean(axis=0)
    radius = float(np.linalg.norm(points, axis=1).max())
    if not np.isfinite(radius) or radius <= 1e-12:
        raise EmbeddingError("embedding_point_geometry_invalid")
    # Library meshes are Z-up; the checkpoint was trained with gravity along Y.
    xyz = (points / radius)[:, [0, 2, 1]].T
    features = np.full((1, 6, POINT_COUNT), 0.4, dtype="<f4")
    features[0, :3] = xyz
    return EmbeddingInput("point_cloud", points=features.tobytes())


def canary_input() -> EmbeddingInput:
    """A deterministic nondegenerate cloud; no fixture file or random global state."""
    import numpy as np

    data = (
        np.random.Generator(np.random.PCG64(166))
        .normal(size=(1, 6, POINT_COUNT))
        .astype("<f4")
    )
    data[:, :3] /= np.linalg.norm(data[:, :3], axis=1).max()
    data[:, 3:] = 0.4
    return EmbeddingInput("point_cloud", points=data.tobytes())


def grouped_points(item: EmbeddingInput):
    """Return (centers [1,3,64], groups [1,9,256,64]) for standard ONNX."""
    import numpy as np

    if item.modality != "point_cloud" or item.points is None:
        raise EmbeddingError("embedding_point_input_invalid")
    features = np.frombuffer(item.points, dtype="<f4").reshape(1, 6, POINT_COUNT)
    if (
        not np.isfinite(features).all()
        or np.max(np.abs(features[:, :3])) > 1.00001
        or np.min(features[:, 3:]) < 0
        or np.max(features[:, 3:]) > 1
    ):
        raise EmbeddingError("embedding_point_input_invalid")
    xyz = features[0, :3].T.copy()
    distances = np.full(POINT_COUNT, np.inf, dtype=np.float32)
    selected = []
    index = 0
    for _ in range(64):
        selected.append(index)
        np.minimum(distances, np.sum((xyz - xyz[index]) ** 2, axis=1), out=distances)
        index = int(np.argmax(distances))
    centers = xyz[selected]
    grouped = []
    for center in centers:
        indices = np.flatnonzero(np.sum((xyz - center) ** 2, axis=1) <= 0.4**2)[:256]
        if len(indices) < 256:
            indices = np.pad(
                indices, (0, 256 - len(indices)), constant_values=int(indices[0])
            )
        grouped.append(
            np.concatenate([xyz[indices] - center, features[0].T[indices]], axis=1)
        )
    return centers.T[None].astype("float32"), np.asarray(
        grouped, dtype="float32"
    ).transpose(2, 1, 0)[None]

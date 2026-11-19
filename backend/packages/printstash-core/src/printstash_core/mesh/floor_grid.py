"""A neutral layer in the model's millimetre coordinates, behind mesh coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.floating[Any]]

CELL_SIZES = (1, 2, 5, 10, 20, 50)
GRID_RGB = (138, 147, 166)
GRID_ALPHA = 0.8


def paint_floor_grid(
    rgba: NDArray[np.uint8],
    rotation: FloatArray,
    bounds_min: FloatArray,
    bounds_max: FloatArray,
    view_center: tuple[float, float] | FloatArray,
    scale: float,
    *,
    scale_y: float | None = None,
    samples: int = 1,
) -> None:
    """Rasterize the plane into uncovered samples of the existing framebuffer."""
    import numpy as np

    height, width = rgba.shape[:2]
    footprint = (np.asarray(bounds_max) - bounds_min)[:2]
    depth = rotation[2]
    # View depth increases away from the camera. From below the bed (including
    # Bottom), the floor would be in front of the model rather than supporting it.
    if depth[2] >= -1e-6:
        return
    plane = bounds_min[2]
    cell = next(
        (size for size in CELL_SIZES if size >= max(footprint) / 5), CELL_SIZES[-1]
    )
    center = (np.asarray(bounds_max)[:2] + np.asarray(bounds_min)[:2]) * 0.5
    radius = footprint * 0.5 + 1.5 * cell
    sy = scale if scale_y is None else scale_y
    right = rotation[0] - depth * rotation[0, 2] / depth[2]
    up = rotation[1] - depth * rotation[1, 2] / depth[2]
    origin = depth * plane / depth[2]
    pixel = np.hypot(right / scale, up / sy) * samples
    columns = np.arange(width, dtype=np.float32) + 0.5
    screen_x = (columns - width * 0.5) / scale + view_center[0]
    for start in range(0, height, 48):
        tile = rgba[start : start + 48]
        rows = np.arange(start, start + len(tile), dtype=np.float32) + 0.5
        screen_y = (height * 0.5 - rows) / sy + view_center[1]
        points = [
            origin[axis]
            + screen_x[None, :] * right[axis]
            + screen_y[:, None] * up[axis]
            for axis in (0, 1)
        ]
        # Fade around the footprint on the floor. A screen-centred grid also
        # draws far behind tall, narrow models, where it looks suspended.
        radial = np.hypot(
            *(
                (point - c) / r
                for point, c, r in zip(points, center, radius, strict=True)
            )
        )
        edge = np.maximum(
            np.abs(columns[None, :] / width - 0.5) * 2,
            np.abs(rows[:, None] / height - 0.5) * 2,
        )
        fade = np.clip((1 - radial) / 0.6, 0, 1) ** 1.1
        fade *= np.clip((0.99 - edge) / 0.11, 0, 1)
        alpha = np.zeros_like(fade)
        for axis, point in enumerate(points):
            distance = np.abs(np.remainder(point + cell * 0.5, cell) - cell * 0.5)
            coverage = np.clip(0.95 - distance / max(pixel[axis], 1e-6), 0, 1)
            np.maximum(alpha, coverage, out=alpha)
        alpha = (alpha * fade * GRID_ALPHA * 255).astype(np.uint8)
        visible = (tile[..., 3] == 0) & (alpha > 0)
        tile[visible, :3] = GRID_RGB
        tile[visible, 3] = alpha[visible]

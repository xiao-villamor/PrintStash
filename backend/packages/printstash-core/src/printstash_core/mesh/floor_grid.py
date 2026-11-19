"""A neutral layer in the model's millimetre coordinates, behind mesh coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .preview_profile import PREVIEW_PROFILE

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

CELL_SIZES = (1, 2, 5, 10, 20, 50)
GRID_RGB = (138, 147, 166)
GRID_ALPHA = 0.8


def paint_floor_grid(
    rgba: NDArray[np.uint8],
    rotation,
    bounds_min,
    bounds_max,
    view_center,
    scale: float,
    *,
    scale_y: float | None = None,
    samples: int = 1,
) -> None:
    """Rasterize the plane into uncovered samples of the existing framebuffer."""
    import numpy as np

    height, width = rgba.shape[:2]
    limits = np.asarray(bounds_max) - bounds_min
    thin = int(np.argmin(limits))
    plane_axes = [axis for axis in range(3) if axis != thin]
    broad = min(limits[plane_axes])
    planar = (
        broad > 1e-6 and limits[thin] / broad <= PREVIEW_PROFILE.flat_thickness_ratio
    )
    axis = thin if planar else 2
    other = [index for index in range(3) if index != axis]
    depth = rotation[2]
    if abs(depth[axis]) < 0.08:
        return
    plane = (bounds_min[axis] + bounds_max[axis]) * 0.5 if planar else bounds_min[2]
    cell = next(
        (size for size in CELL_SIZES if size >= max(limits[other]) / 5), CELL_SIZES[-1]
    )
    sy = scale if scale_y is None else scale_y
    right = rotation[0] - depth * rotation[0, axis] / depth[axis]
    up = rotation[1] - depth * rotation[1, axis] / depth[axis]
    origin = depth * plane / depth[axis]
    pixel = np.hypot(right / scale, up / sy) * samples
    columns = np.arange(width, dtype=np.float32) + 0.5
    screen_x = (columns - width * 0.5) / scale + view_center[0]
    for start in range(0, height, 48):
        tile = rgba[start : start + 48]
        rows = np.arange(start, start + len(tile), dtype=np.float32) + 0.5
        screen_y = (height * 0.5 - rows) / sy + view_center[1]
        radius = np.hypot(
            (columns[None, :] / width - 0.5) * 2,
            (rows[:, None] / height - 0.5) * 2,
        )
        fade = np.clip((0.96 - radius) / 0.65, 0, 1) ** 1.1
        alpha = np.zeros_like(fade)
        for index in other:
            point = (
                origin[index]
                + screen_x[None, :] * right[index]
                + screen_y[:, None] * up[index]
            )
            distance = np.abs(np.remainder(point + cell * 0.5, cell) - cell * 0.5)
            coverage = np.clip(0.95 - distance / max(pixel[index], 1e-6), 0, 1)
            np.maximum(alpha, coverage, out=alpha)
        alpha = (alpha * fade * GRID_ALPHA * 255).astype(np.uint8)
        visible = (tile[..., 3] == 0) & (alpha > 0)
        tile[visible, :3] = GRID_RGB
        tile[visible, 3] = alpha[visible]

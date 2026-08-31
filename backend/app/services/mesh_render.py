"""Application compatibility facade for the core software mesh rasteriser."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Optional

from printstash_core.mesh import rasterizer as _core

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

FLAT_MESH_THICKNESS_RATIO = _core.FLAT_MESH_THICKNESS_RATIO
RasterBudget = _core.RasterBudget

# Preserve the helper import surface used by the STL fallback and focused tests.
_rasterise_triangles = _core._rasterise_triangles


def _select_view_rotation(verts: Any, _np: Any) -> Any:
    return _core._select_view_rotation(verts)


def _front_rotation_for_thin_axis(thin_axis: int, _np: Any) -> Any:
    return _core._front_rotation_for_thin_axis(thin_axis)


def render_thumbnail(
    load_mesh: Callable[[Path], Any],
    path: Path,
    width: int = 640,
    height: int = 480,
) -> Optional[bytes]:
    """Load and render a PNG thumbnail while preserving the legacy API."""
    mesh = load_mesh(path)
    return render_mesh_thumbnail(mesh, path.name, width=width, height=height)


def render_mesh_thumbnail(
    mesh: Any,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    output_format: Literal["PNG", "WEBP"] = "PNG",
) -> Optional[bytes]:
    """Render through core with application settings and logging injected."""
    return _core.render_mesh_thumbnail(
        mesh,
        name,
        width=width,
        height=height,
        face_chunk_size=settings.mesh_render_face_chunk_size,
        logger=logger,
        rasterise_triangles=_rasterise_triangles,
        output_format=output_format,
    )


__all__ = [
    "FLAT_MESH_THICKNESS_RATIO",
    "RasterBudget",
    "render_mesh_thumbnail",
    "render_thumbnail",
]

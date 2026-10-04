"""Application compatibility facade for the core software mesh rasteriser."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

from printstash_core.mesh import rasterizer as _core
from printstash_core.mesh.render_geometry import (
    PreparedRender,
    RenderableMesh,
)
from printstash_core.mesh.render_geometry import (
    prepare_mesh_render as _prepare_mesh_render,
)
from printstash_core.mesh.render_geometry import (
    prepare_scene_render as _prepare_scene_render,
)
from printstash_core.mesh.similarity.components import ExpandedScene

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = get_logger(__name__)

FLAT_MESH_THICKNESS_RATIO = _core.FLAT_MESH_THICKNESS_RATIO
RasterBudget = _core.RasterBudget
RenderedPixels = _core.RenderedPixels
RGBBackground = _core.RGBBackground

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


def prepare_mesh_render(mesh: RenderableMesh | None) -> PreparedRender | None:
    """Prepare one owned geometry snapshot using the application chunk budget."""
    return _prepare_mesh_render(
        mesh, face_chunk_size=settings.mesh_render_face_chunk_size
    )


def prepare_scene_render(scene: ExpandedScene) -> PreparedRender:
    """Prepare retained placements without composing a whole source mesh."""
    return _prepare_scene_render(
        scene, face_chunk_size=settings.mesh_render_face_chunk_size
    )


def render_prepared_thumbnail(
    prepared: PreparedRender,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    output_format: Literal["PNG", "WEBP"] = "PNG",
    view_rotation: NDArray[np.float64] | None = None,
    matte: bool = False,
) -> bytes | None:
    """Render encoded output from shared preparation with application policy."""
    return _core.render_prepared_thumbnail(
        prepared,
        name,
        width=width,
        height=height,
        face_chunk_size=settings.mesh_render_face_chunk_size,
        logger=logger,
        rasterise_triangles=_rasterise_triangles,
        output_format=output_format,
        view_rotation=view_rotation,
        matte=matte,
    )


def render_prepared_pixels(
    prepared: PreparedRender,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    view_rotation: NDArray[np.float64] | None = None,
    matte: bool = False,
) -> RenderedPixels | None:
    """Return direct RGBA pixels with the same application raster policy."""
    return _core.render_prepared_pixels(
        prepared,
        name,
        width=width,
        height=height,
        face_chunk_size=settings.mesh_render_face_chunk_size,
        logger=logger,
        rasterise_triangles=_rasterise_triangles,
        view_rotation=view_rotation,
        matte=matte,
    )


def render_scene_thumbnail(
    scene: ExpandedScene,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    output_format: Literal["PNG", "WEBP"] = "PNG",
) -> Optional[bytes]:
    """Render retained placements through the bounded core scene entry point."""
    return _core.render_scene_thumbnail(
        scene,
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
    "PreparedRender",
    "RenderedPixels",
    "RGBBackground",
    "prepare_mesh_render",
    "prepare_scene_render",
    "render_prepared_thumbnail",
    "render_prepared_pixels",
    "render_mesh_thumbnail",
    "render_scene_thumbnail",
    "render_thumbnail",
]

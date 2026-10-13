"""Application compatibility facade for the core software mesh rasteriser."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

from printstash_core.mesh import rasterizer as _core
from printstash_core.mesh.render_geometry import (
    PreparedRender,
    PreparedRenderGeometry,
    RenderableMesh,
    prepare_mesh,
    prepare_normals,
    prepare_scene,
)
from printstash_core.mesh.similarity.components import ExpandedScene

from app.core.logging import get_logger
from app.core.native_settings import settings

from .gpu_normals import (
    GPU_NORMALS_MIN_FACES,
    RenderInput,
    VisualRender,
    canonical,
    eligible,
)

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
    from app.runtime.compute.client import available

    if available(render=True):
        prepared = prepare_preview_render(mesh)
        if prepared is not None:
            return render_prepared_thumbnail(
                prepared, name, width, height, output_format=output_format
            )
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


def _prepare_geometry(
    geometry: PreparedRenderGeometry, *, min_gpu_faces: int = GPU_NORMALS_MIN_FACES
) -> RenderInput:
    from app.runtime.compute.client import available

    if geometry.face_count >= min_gpu_faces and available(render=True):
        return _prepare_visual_geometry(geometry)
    return prepare_normals(
        geometry, face_chunk_size=settings.mesh_render_face_chunk_size
    )


def _prepare_visual_geometry(geometry: PreparedRenderGeometry) -> RenderInput:
    candidate = VisualRender(geometry)
    if eligible(candidate):
        return candidate
    return canonical(candidate, face_chunk_size=settings.mesh_render_face_chunk_size)


def prepare_preview_packet(mesh: RenderableMesh | None) -> RenderInput | None:
    """Prepare an admitted handoff without consulting the compute owner.

    The broker or guarded CPU fallback consumes these same bounded arrays after
    the parser exits. Preparing them needs no transport, driver or status schema.
    """
    geometry = prepare_mesh(mesh)
    return None if geometry is None else _prepare_visual_geometry(geometry)


def prepare_scene_packet(scene: ExpandedScene) -> RenderInput:
    """Keep placements in a deferred packet, independent of current GPU state."""
    return _prepare_visual_geometry(prepare_scene(scene))


def prepare_mesh_render(mesh: RenderableMesh | None) -> RenderInput | None:
    """Choose the prepared representation before computing CPU smooth normals."""
    geometry = prepare_mesh(mesh)
    return None if geometry is None else _prepare_geometry(geometry)


def prepare_preview_render(mesh: RenderableMesh | None) -> RenderInput | None:
    """Preview-only GPU preparation, independent of canonical analytic views."""
    geometry = prepare_mesh(mesh)
    return None if geometry is None else _prepare_geometry(geometry, min_gpu_faces=1)


def prepare_scene_render(scene: ExpandedScene) -> RenderInput:
    """Retain source placements and select bounded optional normal preparation."""
    return _prepare_geometry(prepare_scene(scene), min_gpu_faces=1)


def render_prepared_thumbnail(
    prepared: RenderInput,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    output_format: Literal["PNG", "WEBP"] = "PNG",
    view_rotation: NDArray[np.float64] | None = None,
    matte: bool = False,
) -> bytes | None:
    """Render encoded output from shared preparation with application policy."""
    from .compute_render import render

    accelerated = render(prepared, width, height, (view_rotation,), matte)
    if accelerated is not None:
        return _core.encode_rendered_pixels(accelerated[0], output_format=output_format)
    return _core.render_prepared_thumbnail(
        canonical(
            prepared, cache=True, face_chunk_size=settings.mesh_render_face_chunk_size
        ),
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
    prepared: RenderInput,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    view_rotation: NDArray[np.float64] | None = None,
    matte: bool = False,
    compute: bool = True,
) -> RenderedPixels | None:
    """Return direct RGBA pixels with the same application raster policy."""
    from .compute_render import render

    accelerated = (
        render(prepared, width, height, (view_rotation,), matte) if compute else None
    )
    if accelerated is not None:
        return accelerated[0]
    return _core.render_prepared_pixels(
        canonical(
            prepared, cache=True, face_chunk_size=settings.mesh_render_face_chunk_size
        ),
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
    from app.runtime.compute.client import available

    if available(render=True):
        return render_prepared_thumbnail(
            prepare_scene_render(scene),
            name,
            width,
            height,
            output_format=output_format,
        )
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
    "RenderInput",
    "RenderedPixels",
    "RGBBackground",
    "prepare_mesh_render",
    "prepare_preview_render",
    "prepare_scene_render",
    "render_prepared_thumbnail",
    "render_prepared_pixels",
    "render_mesh_thumbnail",
    "render_scene_thumbnail",
    "render_thumbnail",
]

"""Application adapter for the required Rust preview engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Optional

from printstash_core.mesh import native_rasterizer
from printstash_core.mesh import rasterizer as _core

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

FLAT_MESH_THICKNESS_RATIO = _core.FLAT_MESH_THICKNESS_RATIO


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
    """Render in Rust; this facade only transfers the mesh and encoded result."""
    native_rasterizer.kernel()
    if mesh is None or mesh.faces is None or len(mesh.faces) == 0:
        return None
    try:
        return native_rasterizer.render_preview(
            mesh,
            width=width,
            height=height,
            chunk=settings.mesh_render_face_chunk_size,
            output_format=output_format,
        )
    except Exception:  # noqa: BLE001 - the engine reports failed derivatives
        logger.warning("mesh_render: Rust preview failed for %s", name, exc_info=True)
        return None


__all__ = [
    "FLAT_MESH_THICKNESS_RATIO",
    "render_mesh_thumbnail",
    "render_thumbnail",
]

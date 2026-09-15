"""Framework-neutral facade for the required Rust preview engine."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from . import native_rasterizer
from .preview_profile import PREVIEW_PROFILE

FLAT_MESH_THICKNESS_RATIO = PREVIEW_PROFILE.flat_thickness_ratio


class LogSink(Protocol):
    def warning(self, msg: object, *args: object, exc_info: bool = False) -> None: ...


def render_mesh_thumbnail(
    mesh: Any,
    name: str,
    width: int = 640,
    height: int = 480,
    *,
    face_chunk_size: int = 64_000,
    logger: LogSink | None = None,
    output_format: Literal["PNG", "WEBP"] = "PNG",
    view_rotation: Any = None,
    matte: bool = False,
) -> bytes | None:
    """Return encoded Rust output, or no derivative for invalid geometry."""
    # Dependency failures must not silently select a different implementation.
    native_rasterizer.kernel()
    if (
        mesh is None
        or mesh.faces is None
        or len(mesh.faces) == 0
        or len(mesh.vertices) == 0
    ):
        if logger is not None:
            logger.warning("mesh_render: empty mesh for %s", name)
        return None
    try:
        return native_rasterizer.render_preview(
            mesh,
            width=width,
            height=height,
            chunk=face_chunk_size,
            output_format=output_format,
            view_rotation=view_rotation,
            matte=matte,
        )
    except ImportError:
        raise
    except Exception:
        if logger is not None:
            logger.warning(
                "mesh_render: render_thumbnail failed for %s", name, exc_info=True
            )
        return None

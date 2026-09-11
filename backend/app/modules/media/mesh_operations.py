"""Mesh operations."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Literal, Optional, Tuple

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.modules.media import mesh_render as mesh_render
from app.modules.media import stl_fallback as stl_fallback
from app.modules.media import stl_streaming as stl_streaming

from .mesh_processing import FallbackThumbnail


def analyze_mesh(
    path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    report: Callable[[str], None] | None = None,
    file_type: str | None = None,
    output_format: Literal["PNG", "WEBP"] = "PNG",
    include_fingerprint: bool = False,
    triangle_cap: int = MAX_ANALYSIS_FACES,
) -> Tuple[Dict[str, Optional[float]], Optional[bytes]]:
    """Extract geometry and render a thumbnail with a single mesh load.

    Returns ``(geometry_dict, png_bytes_or_None)``. *report* receives progress
    labels as the stages run (see ingestion progress hints).
    """

    from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest

    result = ThumbnailEngine().generate(
        ThumbnailRequest(
            path=path,
            file_type=file_type,
            width=width,
            height=height,
            include_geometry=True,
            reason="ingestion",
            report=report,
            output_format=output_format,
            include_fingerprint=include_fingerprint,
            triangle_cap=triangle_cap,
        )
    )
    thumb = result.image
    if thumb is not None and result.strategy.value in ("streaming", "fallback"):
        thumb = FallbackThumbnail(thumb, complete=result.complete)
    if include_fingerprint:
        from app.modules.media.fingerprints import GeometryMetadata

        return GeometryMetadata(result.geometry, result.fingerprint_result), thumb
    return result.geometry, thumb


def render_thumbnail(
    path: Path, width: int | None = None, height: int | None = None
) -> Optional[bytes]:
    """Render a PNG thumbnail of *path*. Returns PNG bytes or None on failure."""
    from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest

    result = ThumbnailEngine().generate(
        ThumbnailRequest(
            path=path,
            width=width,
            height=height,
            include_geometry=False,
            reason="repair",
        )
    )
    if result.image is None:
        return None
    if result.strategy.value in ("streaming", "fallback"):
        return FallbackThumbnail(result.image, complete=result.complete)
    return result.image

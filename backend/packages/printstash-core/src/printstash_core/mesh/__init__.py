"""Framework-neutral mesh rendering and geometric normalization."""

from .preview_profile import PREVIEW_PROFILE, PreviewProfile
from .rasterizer import (
    FLAT_MESH_THICKNESS_RATIO,
    render_mesh_thumbnail,
)

__all__ = [
    "FLAT_MESH_THICKNESS_RATIO",
    "PREVIEW_PROFILE",
    "PreviewProfile",
    "render_mesh_thumbnail",
]

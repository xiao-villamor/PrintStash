"""Framework-neutral mesh rendering and geometric normalization."""

from .preview_profile import PREVIEW_PROFILE, PreviewProfile
from .rasterizer import (
    FLAT_MESH_THICKNESS_RATIO,
    RasterBudget,
    RenderedPixels,
    RGBBackground,
    render_mesh_thumbnail,
    render_prepared_pixels,
    render_prepared_thumbnail,
    render_scene_thumbnail,
)
from .render_geometry import PreparedRender, prepare_mesh_render, prepare_scene_render

__all__ = [
    "FLAT_MESH_THICKNESS_RATIO",
    "PREVIEW_PROFILE",
    "PreviewProfile",
    "RasterBudget",
    "RGBBackground",
    "RenderedPixels",
    "PreparedRender",
    "prepare_mesh_render",
    "prepare_scene_render",
    "render_prepared_pixels",
    "render_prepared_thumbnail",
    "render_mesh_thumbnail",
    "render_scene_thumbnail",
]

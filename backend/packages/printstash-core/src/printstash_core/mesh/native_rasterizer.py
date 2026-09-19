"""Required Rust rendering jobs; Python only transfers inputs and outputs."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any


def kernel() -> ModuleType:
    """Load the required extension, propagating missing or broken installations."""
    return importlib.import_module("printstash_mesh_native")


def render_preview(
    mesh: Any,
    *,
    width: int = 640,
    height: int = 480,
    chunk: int = 64000,
    output_format: str = "PNG",
    view_rotation: Any = None,
    matte: bool = False,
) -> bytes:
    """Transport mesh buffers into one Rust job; return the encoded preview."""
    import numpy as np

    from .preview_profile import PREVIEW_PROFILE as p

    native = kernel()
    prepared = getattr(mesh, "_printstash_native_preview", None)
    if prepared is not None:
        image, _seconds = prepared.render_preview(
            width,
            height,
            output_format,
            (
                p.margin_fraction,
                p.hero_azimuth_degrees,
                p.hero_elevation_degrees,
                p.flat_tilt_degrees,
                p.flat_thickness_ratio,
                *p.material_albedo,
            ),
            (
                p.supersample_max_output_width,
                p.supersample_small_factor,
                p.supersample_large_factor,
            ),
            None
            if view_rotation is None
            else np.asarray(view_rotation, dtype=np.float64).tolist(),
            matte,
        )
        return image
    image, _seconds = native.render_preview(
        np.asarray(mesh.vertices, dtype=np.float32).tobytes(),
        np.asarray(mesh.faces, dtype=np.int64).tobytes(),
        width,
        height,
        max(int(chunk), 1),
        output_format,
        (
            p.margin_fraction,
            p.hero_azimuth_degrees,
            p.hero_elevation_degrees,
            p.flat_tilt_degrees,
            p.flat_thickness_ratio,
            *p.material_albedo,
        ),
        (
            p.supersample_max_output_width,
            p.supersample_small_factor,
            p.supersample_large_factor,
        ),
        None
        if view_rotation is None
        else np.asarray(view_rotation, dtype=np.float64).tolist(),
        matte,
    )
    return image


def render_views(mesh: Any, width: int, height: int, frames: Any) -> list[bytes]:
    """Transfer one mesh for all inference views; Rust shares preparation."""
    import numpy as np

    from .preview_profile import PREVIEW_PROFILE as p

    native = kernel()
    return native.render_views(
        np.asarray(mesh.vertices, dtype=np.float32).tobytes(),
        np.asarray(mesh.faces, dtype=np.int64).tobytes(),
        width,
        height,
        64000,
        (
            p.margin_fraction,
            p.hero_azimuth_degrees,
            p.hero_elevation_degrees,
            p.flat_tilt_degrees,
            p.flat_thickness_ratio,
            *p.material_albedo,
        ),
        (
            p.supersample_max_output_width,
            p.supersample_small_factor,
            p.supersample_large_factor,
        ),
        frames,
    )

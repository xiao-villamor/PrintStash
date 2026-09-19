"""NumPy shape descriptors, with versioned SH projection and six real views.

The projection basis is a checked-in calibration artifact. Missing or corrupt
assets never turn an uncalibrated spectrum into a supposedly ready descriptor.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from importlib.resources import files
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ..rasterizer import render_mesh_thumbnail
from .fingerprint import GeometryError, canonical_sample_triangles
from .geometry import Surface
from .hull import hull_volume

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]

SH_RECIPE = "occupancy64-shells32-degree16-pca64-v1"
# Resampling changes can change thresholded view hashes. Record the actual
# renderer so native views never masquerade as the legacy NumPy/Pillow recipe.
VIEW_RECIPE = "pca-six-orthographic64-matte-dct8-rust-v2"


@dataclass(frozen=True)
class ShapeDescriptors:
    sh: tuple[float, ...] | None
    sh_basis_digest: str | None
    view_hashes: bytes | None
    hull_ratio: float | None
    fill_ratio: float | None
    inertia_ratios: tuple[float, ...] | None
    unavailable: tuple[tuple[str, str], ...]


def describe_surface(
    surface: Surface, *, volume: float | None, ambiguous_frame: bool
) -> ShapeDescriptors:
    import numpy as np

    missing: list[tuple[str, str]] = []
    sh: tuple[float, ...] | None = None
    digest: str | None = None
    views: bytes | None = None
    hull_ratio: float | None = None
    fill_ratio: float | None = None
    inertia: tuple[float, ...] | None = None
    try:
        sh, digest = project_sh(sh_spectrum(surface, fill=volume is not None))
    except GeometryError as exc:
        missing.append(("sh", exc.code))
    try:
        views = view_hashes(surface, ambiguous_frame=ambiguous_frame)
    except GeometryError as exc:
        missing.append(("view_hashes", exc.code))
    if volume is None:
        missing.extend(
            (name, "invalid_volume")
            for name in ("hull_ratio", "fill_ratio", "inertia_ratios")
        )
    else:
        try:
            hull_ratio = volume / hull_volume(surface.vertices)
        except GeometryError as exc:
            missing.append(("hull_ratio", exc.code))
        if ambiguous_frame:
            missing.append(("fill_ratio", "ambiguous_frame"))
        else:
            extents = np.ptp(surface.vertices @ surface.frame, axis=0)
            fill_ratio = volume / float(np.prod(extents))
        inertia = volume_inertia_ratios(surface)
    return ShapeDescriptors(
        sh, digest, views, hull_ratio, fill_ratio, inertia, tuple(missing)
    )


def volume_inertia_ratios(surface: Surface) -> tuple[float, ...]:
    """Signed tetrahedral volume integrals; callers first establish valid volume."""
    import numpy as np

    from ..native_rasterizer import kernel

    triangles = np.asarray(surface.vertices[surface.faces], dtype="=f8")
    try:
        return tuple(kernel().volume_inertia_ratios(triangles.tobytes()))
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc


def sh_spectrum(surface: Surface, *, fill: bool) -> FloatArray:
    """32 radial shells × 17 rotation-invariant spherical harmonic powers.

    Associated Legendre recurrence is evaluated one order at a time; no array
    proportional to voxel-count × harmonic-count is allocated.
    """
    import numpy as np

    from ..native_rasterizer import kernel

    try:
        packed = kernel().sh_spectrum(
            np.asarray(surface.vertices, dtype="=f8").tobytes(),
            np.asarray(surface.faces, dtype="=i8").tobytes(),
            fill,
        )
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc
    return np.frombuffer(packed, dtype="=f8").copy()


def project_sh(spectrum: FloatArray) -> tuple[tuple[float, ...], str]:
    import numpy as np

    package = files("printstash_core.mesh.similarity")
    try:
        manifest = json.loads(package.joinpath("sh_basis.json").read_text())
        blob = package.joinpath("sh_basis.npz").read_bytes()
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GeometryError("uncalibrated_basis") from exc
    digest = hashlib.sha256(blob).hexdigest()
    if (
        not isinstance(manifest, dict)
        or digest != manifest.get("sha256")
        or manifest.get("recipe") != SH_RECIPE
    ):
        raise GeometryError("invalid_basis_digest")
    try:
        with np.load(io.BytesIO(blob), allow_pickle=False) as basis:
            mean, components = basis["mean"], basis["components"]
            if (
                mean.shape != (544,)
                or components.shape != (64, 544)
                or not np.isfinite(components).all()
                or not np.isfinite(mean).all()
            ):
                raise GeometryError("invalid_basis_shape")
            projected = components @ (spectrum - mean)
    except (KeyError, ValueError, OSError) as exc:
        if isinstance(exc, GeometryError):
            raise
        raise GeometryError("invalid_basis_shape") from exc
    norm = float(np.linalg.norm(projected))
    return tuple(
        float(x) for x in (projected / norm if norm else projected).astype(np.float32)
    ), digest


def view_hashes(surface: Surface, *, ambiguous_frame: bool) -> bytes:
    import numpy as np
    from PIL import Image  # pyright: ignore[reportMissingTypeStubs]

    diagonal = float(np.linalg.norm(np.ptp(surface.vertices @ surface.frame, axis=0)))
    normalized = surface.vertices @ surface.frame / diagonal
    if ambiguous_frame:
        triangles = normalized[surface.faces]
    else:
        triangles = canonical_sample_triangles(normalized, surface.faces)
    mesh = SimpleNamespace(
        vertices=triangles.reshape((-1, 3)),
        faces=np.arange(triangles.size // 3).reshape((-1, 3)),
    )
    output = bytearray()
    for axis in range(3):
        for sign in (-1, 1):
            view = np.eye(3)[[(axis + 1) % 3, (axis + 2) % 3, axis]]
            view[0] *= sign
            view[2] *= sign
            png = render_mesh_thumbnail(
                mesh,
                "geometry-descriptor",
                width=64,
                height=64,
                view_rotation=view,
                matte=True,
            )
            if png is None:
                raise GeometryError("view_render_unavailable")
            with Image.open(io.BytesIO(png)) as image:
                pixels = np.asarray(image.convert("L"), dtype=np.float64)
            output.extend(dct_hash(pixels))
    return bytes(output)


def dct_hash(pixels: FloatArray) -> bytes:
    import numpy as np

    from ..native_rasterizer import kernel

    if pixels.shape != (64, 64) or not np.isfinite(pixels).all():
        raise GeometryError("invalid_view_image")
    try:
        return bytes(kernel().dct_hash(np.asarray(pixels, dtype="=f8").tobytes()))
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc

"""NumPy shape descriptors, with versioned SH projection and six real views.

The projection basis is a checked-in calibration artifact. Missing or corrupt
assets never turn an uncalibrated spectrum into a supposedly ready descriptor.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass
from importlib.resources import files
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ..rasterizer import render_mesh_thumbnail
from .fingerprint import GeometryError, canonical_geometry_keys
from .geometry import Surface
from .voxel import voxelize

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    FloatArray = NDArray[np.float64]

SH_RECIPE = "occupancy64-shells32-degree16-pca64-v1"
VIEW_RECIPE = "pca-six-orthographic64-matte-dct8-v1"


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


def hull_volume(vertices: FloatArray, *, max_work: int = 20_000_000) -> float:
    """Incremental convex hull with a hard work ceiling and deterministic order."""
    import numpy as np

    if type(max_work) is not int or not 1 <= max_work <= 20_000_000:
        raise GeometryError("invalid_hull_budget")
    first = int(np.argmin(vertices[:, 0]))
    second = int(np.argmax(np.linalg.norm(vertices - vertices[first], axis=1)))
    axis = vertices[second] - vertices[first]
    third = int(
        np.argmax(np.linalg.norm(np.cross(vertices - vertices[first], axis), axis=1))
    )
    normal = np.cross(vertices[third] - vertices[first], axis)
    fourth = int(np.argmax(np.abs((vertices - vertices[first]) @ normal)))
    chosen = np.array([first, second, third, fourth])
    scale = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    if abs(float((vertices[fourth] - vertices[first]) @ normal)) <= scale**3 * 1e-12:
        raise GeometryError("degenerate_hull")
    interior = vertices[chosen].mean(axis=0)
    facets = chosen[np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])]
    tested = 0
    for point in range(len(vertices)):
        triangles = vertices[facets]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        inward = np.einsum("ij,ij->i", normals, interior - triangles[:, 0]) > 0
        facets[inward] = facets[inward, ::-1]
        normals[inward] *= -1
        tested += len(facets)
        if tested > max_work:
            raise GeometryError("hull_resource_limit")
        visible = (
            np.einsum("ij,ij->i", normals, vertices[point] - triangles[:, 0])
            > np.linalg.norm(normals, axis=1) * scale * 1e-10
        )
        if not np.any(visible):
            continue
        edges = facets[visible][:, ((0, 1), (1, 2), (2, 0))].reshape((-1, 2))
        unique, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
        horizon = unique[counts == 1]
        new = np.column_stack((horizon, np.full(len(horizon), point)))
        facets = np.vstack((facets[~visible], new))
    tri = vertices[facets] - interior
    return float(
        np.abs(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2]))).sum()
        / 6
    )


def volume_inertia_ratios(surface: Surface) -> tuple[float, ...]:
    """Signed tetrahedral volume integrals; callers first establish valid volume."""
    import numpy as np

    tri = surface.vertices[surface.faces]
    volumes = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])) / 6
    volume = float(volumes.sum())
    sums = tri.sum(axis=1)
    centroid = np.einsum("fi,f->i", sums, volumes) / (4 * volume)
    second = (
        np.einsum("fvi,fvj,f->ij", tri, tri, volumes)
        + np.einsum("fi,fj,f->ij", sums, sums, volumes)
    ) / (20 * volume)
    covariance = second - np.outer(centroid, centroid)
    inertia = np.trace(covariance) * np.eye(3) - covariance
    eigenvalues = np.linalg.eigvalsh(inertia)
    return tuple(float(x) for x in eigenvalues / eigenvalues[-1])


def sh_spectrum(surface: Surface, *, fill: bool) -> FloatArray:
    """32 radial shells × 17 rotation-invariant spherical harmonic powers.

    Associated Legendre recurrence is evaluated one order at a time; no array
    proportional to voxel-count × harmonic-count is allocated.
    """
    import numpy as np

    radius = float(np.linalg.norm(surface.vertices, axis=1).max()) * 1.01
    occupancy = voxelize(surface.vertices, surface.faces, half_width=radius, fill=fill)
    xyz = (np.argwhere(occupancy).astype(np.float64) + 0.5) / 32 - 1
    distance = np.linalg.norm(xyz, axis=1)
    inside = (distance > 0) & (distance <= 1)
    xyz, distance = xyz[inside], distance[inside]
    if not len(xyz):
        raise GeometryError("empty_occupancy")
    shells = np.minimum((distance * 32).astype(np.int64), 31)
    z = xyz[:, 2] / distance
    phi = np.arctan2(xyz[:, 1], xyz[:, 0])
    power = np.zeros((32, 17), dtype=np.float64)
    diagonal = np.ones(len(xyz))
    normalization = float(len(xyz))
    for order in range(17):
        if order:
            diagonal *= -(2 * order - 1) * np.sqrt(np.maximum(1 - z * z, 0))
        previous = np.zeros(len(xyz))
        current = diagonal.copy()
        cosine, sine = np.cos(order * phi), np.sin(order * phi)
        for degree in range(order, 17):
            if degree > order:
                following = (
                    (2 * degree - 1) * z * current - (degree + order - 1) * previous
                ) / (degree - order)
                previous, current = current, following
            coefficient = math.sqrt(
                (2 * degree + 1)
                / (4 * math.pi)
                * math.factorial(degree - order)
                / math.factorial(degree + order)
            )
            real = (
                np.bincount(shells, weights=current * cosine, minlength=32)
                * coefficient
                / normalization
            )
            imaginary = (
                np.bincount(shells, weights=current * sine, minlength=32)
                * coefficient
                / normalization
            )
            power[:, degree] += (real * real + imaginary * imaginary) * (
                1 if order == 0 else 2
            )
    return np.sqrt(power).ravel()


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
    from PIL import Image

    diagonal = float(np.linalg.norm(np.ptp(surface.vertices @ surface.frame, axis=0)))
    normalized = surface.vertices @ surface.frame / diagonal
    if ambiguous_frame:
        triangles = normalized[surface.faces]
    else:
        _, triangles, _ = canonical_geometry_keys(normalized, surface.faces, diagonal)
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

    if pixels.shape != (64, 64) or not np.isfinite(pixels).all():
        raise GeometryError("invalid_view_image")
    basis = np.cos(np.pi / 64 * (np.arange(64) + 0.5)[None, :] * np.arange(8)[:, None])
    coefficients = basis @ pixels @ basis.T
    values = coefficients.ravel()
    threshold = np.median(values[1:])
    bits = values > threshold
    bits[0] = False
    return np.packbits(bits, bitorder="big").tobytes()

"""Bounded-memory STL thumbnail fallback used when full mesh loading is unsafe."""

from __future__ import annotations

import io
import math
from array import array
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

from app.modules.media.stl_reader import (
    InvalidSTL,
    STLReadLimits,
    STLSourceChanged,
    check_deadline,
    iter_stl_blocks,
    snapshot_stl,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass(frozen=True)
class STLThumbnailResult:
    png: bytes
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    triangle_count: int
    sampled_triangles: int
    source_complete: bool
    scanned_bytes: int
    parsed_triangles: int
    complete: bool
    raster_candidates: int


@dataclass
class STLSample:
    """Retained facets with complete validated source bounds and coverage.

    source_complete certifies source facet coordinates and EOF, not binary
    stored normals or closed topology. complete additionally covers all facets.
    """

    coordinates: array
    triangle_count: int
    sampled_triangles: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    scanned_bytes: int
    parsed_triangles: int
    complete: bool
    source_complete: bool


# Source validation and retained representation have independent budgets.
# All source facets are validated; at most this many are retained for rendering.
_MAX_SAMPLED_TRIANGLES = 100_000
_COVERAGE_CHUNK_TRIANGLES = 2_048
_MAX_RENDER_DIMENSION = 2048
_MAX_COVERAGE_CANDIDATES = 2_000_000
# Keep the footprint deliberately small: a sparse bounded sample of a
# microfaceted surface needs coverage, but must not turn the fallback into a
# silhouette mask that erases meaningful holes.
_MIN_SPLAT_RADIUS = 0.65
_MAX_SPLAT_RADIUS = 1.0


def _sample_priorities(indices: NDArray[np.uint64]) -> NDArray[np.uint64]:
    """A fixed bijection over source facet indices, independent of block size."""
    import numpy as np

    values = indices + np.uint64(0x9E3779B97F4A7C15)
    values = (values ^ (values >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    values = (values ^ (values >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return values ^ (values >> np.uint64(31))


class _FacetSample:
    """Vectorized retained facets, bounded by sample budget plus reader block."""

    def __init__(self, budget: int) -> None:
        import numpy as np

        self.budget = budget
        self.indices = np.empty(0, dtype=np.uint64)
        self.facets = np.empty((0, 3, 3), dtype=np.float64)
        self.first = None
        self.last = None
        self.parsed = 0

    def add(self, facets: NDArray[np.float64]) -> None:
        import numpy as np

        if self.first is None:
            self.first = facets[0].copy()
        self.last = facets[-1].copy()
        incoming = np.arange(self.parsed, self.parsed + len(facets), dtype=np.uint64)
        self.parsed += len(facets)
        if self.budget == 1:
            return
        keep = incoming != 0
        indices = np.concatenate((self.indices, incoming[keep]))
        candidates = np.concatenate((self.facets, facets[keep]))
        retained = self.budget - 1
        if len(indices) > retained:
            selected = np.argpartition(_sample_priorities(indices), retained - 1)[
                :retained
            ]
            indices = indices[selected]
            candidates = candidates[selected]
        self.indices, self.facets = indices, candidates

    def finish(self) -> NDArray[np.float64]:
        import numpy as np

        if self.first is None or self.last is None:
            raise InvalidSTL("empty STL sample")
        if self.parsed == 1 or self.budget == 1:
            return np.asarray([self.first], dtype=np.float64)
        keep = self.indices != self.parsed - 1
        indices = self.indices[keep]
        facets = self.facets[keep]
        interior = self.budget - 2
        if len(indices) > interior:
            selected = np.argpartition(_sample_priorities(indices), interior)[:interior]
            indices, facets = indices[selected], facets[selected]
        order = np.argsort(indices)
        return np.concatenate(
            (np.asarray([self.first]), facets[order], np.asarray([self.last]))
        )


def read_stl_sample(
    path: Path, *, max_triangles: int, limits: STLReadLimits | None = None
) -> STLSample:
    """Validate every source facet and retain a bounded deterministic subset.

    This canonical seam preserves typed source/budget/snapshot failures. Legacy
    preview and analysis adapters still map them to None until their outcome
    contracts migrate together.
    """
    import numpy as np

    if (
        type(max_triangles) is not int
        or not 1 <= max_triangles <= _MAX_SAMPLED_TRIANGLES
    ):
        raise ValueError("invalid_stl_sample_budget")
    lower = np.full(3, np.inf, dtype=np.float64)
    upper = np.full(3, -np.inf, dtype=np.float64)
    retained = _FacetSample(max_triangles)
    bounded = limits if limits is not None else STLReadLimits()
    source = snapshot_stl(path)
    for facets in iter_stl_blocks(path, bounded, snapshot=source):
        lower = np.minimum(lower, facets.min(axis=(0, 1)))
        upper = np.maximum(upper, facets.max(axis=(0, 1)))
        retained.add(facets)
    selected = retained.finish()
    coordinates = array("d")
    coordinates.frombytes(selected.tobytes())
    check_deadline(bounded)
    if snapshot_stl(path) != source:
        raise STLSourceChanged("source changed before completing sample")
    return STLSample(
        coordinates=coordinates,
        triangle_count=retained.parsed,
        sampled_triangles=len(selected),
        bounds_min=(float(lower[0]), float(lower[1]), float(lower[2])),
        bounds_max=(float(upper[0]), float(upper[1]), float(upper[2])),
        scanned_bytes=source.size,
        parsed_triangles=retained.parsed,
        complete=len(selected) == retained.parsed,
        source_complete=True,
    )


def _read_samples(
    path: Path, budget: int, *, limits: STLReadLimits | None = None
) -> STLSample | None:
    try:
        return read_stl_sample(path, max_triangles=budget, limits=limits)
    except (InvalidSTL, OSError):
        return None


def sample_stl_geometry(
    path: Path, *, max_triangles: int = 10_000, limits: STLReadLimits | None = None
) -> STLSample | None:
    """The same bounded sampler used for previews, exposed for partial analysis.

    Every successful sample has validated the complete source. ``source_complete``
    certifies that read; ``complete`` certifies that every source facet is retained.
    Neither flag certifies closed topology.
    """
    if (
        type(max_triangles) is not int
        or not 1 <= max_triangles <= _MAX_SAMPLED_TRIANGLES
    ):
        raise ValueError("invalid_stl_sample_budget")
    return _read_samples(path, max_triangles, limits=limits)


def render_stl_thumbnail(
    path: Path,
    *,
    width: int = 640,
    height: int = 480,
    max_triangles: int | None = None,
) -> STLThumbnailResult | None:
    """Read and rasterise a bounded, spatially covered STL representation.

    Binary and ASCII sources are validated to EOF within byte, line and facet
    budgets while retaining a deterministic bounded sample. Selected facets are rasterised into a
    coarse z-buffer using their actual triangle area, then upscaled. This keeps
    CPU/memory bounded without turning triangles into bounding-box blobs.
    """
    try:
        import numpy as np
        from PIL import Image

        from app.modules.media.mesh_render import (
            RasterBudget,
            _rasterise_triangles,
            _select_view_rotation,
        )
    except ImportError:
        return None

    if not (1 <= width <= _MAX_RENDER_DIMENSION) or not (
        1 <= height <= _MAX_RENDER_DIMENSION
    ):
        return None
    requested_budget = (
        _MAX_SAMPLED_TRIANGLES if max_triangles is None else max(max_triangles, 1)
    )
    work_budget = min(requested_budget, _MAX_SAMPLED_TRIANGLES)
    sampled = _read_samples(path, work_budget)
    if sampled is None:
        return None

    try:
        triangles = np.frombuffer(sampled.coordinates, dtype=np.float64).reshape(
            (-1, 3, 3)
        )
        corners = np.asarray(
            list(
                product(
                    *zip(
                        sampled.bounds_min,
                        sampled.bounds_max,
                        strict=True,
                    )
                )
            ),
            dtype=np.float64,
        )
        if not np.isfinite(triangles).all() or not np.isfinite(corners).all():
            return None
        center = (np.asarray(sampled.bounds_min) + np.asarray(sampled.bounds_max)) * 0.5
        if not np.isfinite(center).all():
            return None
        rotation = _select_view_rotation(corners - center, np).astype(np.float64)
        view_corners = (corners - center) @ rotation.T
        if not np.isfinite(rotation).all() or not np.isfinite(view_corners).all():
            return None
        extent_x = max(float(np.ptp(view_corners[:, 0])), 1e-6)
        extent_y = max(float(np.ptp(view_corners[:, 1])), 1e-6)
        # Keep the coarse fallback's denser internal frame so sparse annular
        # samples remain connected. The persistence normalizer then places the
        # result on the canonical 10% profile canvas.
        margin = 0.18
        scale = min(
            width * (1 - 2 * margin) / extent_x,
            height * (1 - 2 * margin) / extent_y,
        )
        view_mid = (view_corners.max(axis=0) + view_corners.min(axis=0)) * 0.5
        if not math.isfinite(scale) or scale <= 0 or not np.isfinite(view_mid).all():
            return None
    except (FloatingPointError, ValueError, RuntimeError):
        return None

    # Half-resolution coverage keeps the silhouette detailed while actual
    # triangle tests preserve holes. Small meshes retain the same resolution so
    # tiny facets are not rounded away entirely.
    coverage_width = max(1, min(width, max(64, width // 2)))
    coverage_height = max(1, min(height, max(48, height // 2)))
    coarse_image = np.zeros((coverage_height, coverage_width, 3), dtype=np.uint8)
    coarse_zbuffer = np.full(
        (coverage_height, coverage_width), np.inf, dtype=np.float64
    )
    raster_budget = RasterBudget(limit=_MAX_COVERAGE_CANDIDATES)
    base_color = np.asarray(PREVIEW_PROFILE.material_albedo, dtype=np.float32) * 255.0
    light = np.asarray([-0.45, 0.6, 1.0], dtype=np.float32)
    light /= np.linalg.norm(light)

    def shade(normals):
        diffuse = np.clip(normals @ light, 0.0, 1.0)[:, None]
        return np.clip(0.32 + diffuse * 0.68, 0.0, 1.0)

    coarse_scale_x = coverage_width / width
    coarse_scale_y = coverage_height / height
    # A sparse sample of a very large mesh leaves gaps between its facet
    # centroids.  Use the projected sample density to choose a conservative
    # footprint for sub-pixel facets.  The hard upper bound makes the worst
    # case one 4x4 raster candidate box per sampled triangle (1.6m at the 100k
    # sample cap; raster boxes are inclusive), leaving budget for true source
    # triangles under the shared 2m candidate limit.
    projected_model_area = max(
        extent_x * extent_y * scale * scale * coarse_scale_x * coarse_scale_y,
        1.0,
    )
    sample_spacing = math.sqrt(projected_model_area / max(sampled.sampled_triangles, 1))
    splat_radius = min(
        _MAX_SPLAT_RADIUS,
        max(_MIN_SPLAT_RADIUS, 0.7 * sample_spacing),
    )
    sparse_sample = (
        sampled.triangle_count > sampled.sampled_triangles or not sampled.complete
    )

    def accumulate_chunk(chunk) -> None:
        view = (chunk - center) @ rotation.T
        screen = np.empty_like(view)
        screen[:, :, 0] = (view[:, :, 0] - view_mid[0]) * scale + width * 0.5
        screen[:, :, 1] = height * 0.5 - (view[:, :, 1] - view_mid[1]) * scale
        screen[:, :, 2] = view[:, :, 2]
        valid = np.isfinite(screen).all(axis=(1, 2))
        raw_normal = np.cross(view[:, 1] - view[:, 0], view[:, 2] - view[:, 0])
        normal_length = np.linalg.norm(raw_normal, axis=1)
        area = np.abs(
            (screen[:, 1, 0] - screen[:, 0, 0]) * (screen[:, 2, 1] - screen[:, 0, 1])
            - (screen[:, 2, 0] - screen[:, 0, 0]) * (screen[:, 1, 1] - screen[:, 0, 1])
        )
        valid &= np.isfinite(area) & (area > 1e-9) & (normal_length > 1e-12)
        if not valid.any():
            return
        ids = np.flatnonzero(valid)
        coarse_screen = screen[ids].copy()
        coarse_screen[:, :, 0] *= coarse_scale_x
        coarse_screen[:, :, 1] *= coarse_scale_y
        normals = raw_normal[ids] / normal_length[ids, None]
        normals = np.where(normals[:, 2:3] >= 0, normals, -normals)

        # The ordinary rasteriser intentionally tests the true triangle area.
        # For a dense model whose bounded sample contains microfacets that are
        # much smaller than a pixel, that turns a connected surface into a
        # point cloud.  Augment every retained source facet with a tiny
        # screen-space triangle centred on it when the sample is incomplete.
        # The source triangles stay in the input, preserving long/slender
        # facets and their true z-buffer coverage.  Centroids are rendered
        # first so the shared budget reserves bounded coverage work before a
        # large projected facet can consume it.
        if sparse_sample:
            centers = coarse_screen.mean(axis=1)
            radius = np.asarray(splat_radius, dtype=coarse_screen.dtype)
            top = centers.copy()
            top[:, 1] -= radius
            bottom_right = centers.copy()
            bottom_right[:, 0] += radius
            bottom_right[:, 1] += radius
            bottom_left = centers.copy()
            bottom_left[:, 0] -= radius
            bottom_left[:, 1] += radius
            # A single triangle gives every retained facet a symmetric enough
            # centroid footprint while halving raster candidates versus a
            # square made from two triangles. The radius is capped so each
            # candidate box stays at most 4x4 pixels, leaving the shared budget
            # for source facets as well.
            splat_triangles = np.stack((top, bottom_right, bottom_left), axis=1)
            coarse_screen = np.concatenate((splat_triangles, coarse_screen), axis=0)
            normals = np.concatenate((normals, normals), axis=0)

        _rasterise_triangles(
            coarse_image,
            coarse_zbuffer,
            coarse_screen,
            normals[:, None, :].repeat(3, axis=1),
            shade,
            base_color,
            coverage_width,
            coverage_height,
            budget=raster_budget,
        )

    for start in range(0, triangles.shape[0], _COVERAGE_CHUNK_TRIANGLES):
        accumulate_chunk(triangles[start : start + _COVERAGE_CHUNK_TRIANGLES])

    if not np.isfinite(coarse_zbuffer).any():
        return None
    alpha = np.where(np.isfinite(coarse_zbuffer), 255, 0).astype(np.uint8)
    image = np.asarray(
        Image.fromarray(coarse_image, mode="RGB").resize(
            (width, height), Image.Resampling.BILINEAR
        ),
        dtype=np.uint8,
    ).copy()
    alpha = np.asarray(
        Image.fromarray(alpha, mode="L").resize(
            (width, height), Image.Resampling.BILINEAR
        ),
        dtype=np.uint8,
    )
    rgba = np.dstack([image, alpha])
    output = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(output, format="PNG", optimize=True)
    return STLThumbnailResult(
        png=output.getvalue(),
        bounds_min=sampled.bounds_min,
        bounds_max=sampled.bounds_max,
        triangle_count=sampled.triangle_count,
        sampled_triangles=sampled.sampled_triangles,
        scanned_bytes=sampled.scanned_bytes,
        parsed_triangles=sampled.parsed_triangles,
        complete=sampled.complete and raster_budget.used < raster_budget.limit,
        source_complete=sampled.source_complete,
        raster_candidates=raster_budget.used,
    )

"""Disposable two-pass STL thumbnail worker.

This module intentionally has no FastAPI or database dependencies.  The parent
process supplies all budgets on the command line and accepts output only when
the worker exits successfully and writes a complete manifest.  A pass keeps
only bounded chunk arrays and exact source bounds.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from app.modules.media.stl_reader import (
    InvalidSTL as _InvalidSTL,
)
from app.modules.media.stl_reader import (
    STLBudgetExceeded as _BudgetExceeded,
)
from app.modules.media.stl_reader import (
    STLMeasurements,
    STLReadLimits,
    STLSourceChanged,
    STLSourceSnapshot,
    iter_stl_blocks,
    snapshot_stl,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


_WORKER_VERSION = 1
_MAX_RENDER_DIMENSION = 2048
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_TRIANGLES = 20_000_000
_MAX_SOURCE_BYTES = 1 << 30
_MAX_CANDIDATES = 20_000_000
_MAX_CHUNK_TRIANGLES = 8192
_MAX_LINES = 10_000_000
_MAX_LINE_BYTES = 64 * 1024
_MAX_TIMEOUT_SECONDS = 45.0
_MAX_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024


def _apply_worker_limits(
    address_space: int, cpu_seconds: int, *, expected_parent_pid: int
) -> None:
    """Apply limits before importing NumPy/Pillow and protect parent death."""

    if expected_parent_pid < 1:
        raise _InvalidSTL("invalid expected parent pid")
    original_ppid = os.getppid()
    if original_ppid != expected_parent_pid:
        raise _InvalidSTL("parent does not match expected launcher")
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 2))
    except (ImportError, OSError, ValueError):  # pragma: no cover - platform dependent
        pass
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            libc = ctypes.CDLL(None)
            # Linux PR_SET_PDEATHSIG = 1. If the API process disappears, the
            # worker is killed instead of becoming an orphaned renderer.
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
                raise _InvalidSTL("could not install parent-death signal")
        except (
            AttributeError,
            OSError,
            TypeError,
        ) as exc:  # pragma: no cover - platform dependent
            raise _InvalidSTL("could not install parent-death signal") from exc
        current_ppid = os.getppid()
        if current_ppid != expected_parent_pid or current_ppid != original_ppid:
            raise _InvalidSTL("parent changed during worker initialization")


@dataclass(frozen=True)
class _Limits:
    max_triangles: int
    max_source_bytes: int
    max_candidates: int
    chunk_triangles: int
    max_lines: int
    max_line_bytes: int
    deadline: float


@dataclass(frozen=True)
class _PassStats:
    snapshot: STLSourceSnapshot
    triangle_count: int
    scanned_bytes: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]


def _check_deadline(limits: _Limits) -> None:
    if time.monotonic() >= limits.deadline:
        raise _BudgetExceeded("deadline")


def _reader_limits(limits: _Limits) -> STLReadLimits:
    return STLReadLimits(
        max_triangles=limits.max_triangles,
        max_source_bytes=limits.max_source_bytes,
        chunk_triangles=limits.chunk_triangles,
        max_lines=limits.max_lines,
        max_line_bytes=limits.max_line_bytes,
        deadline=limits.deadline,
    )


def _read_pass(
    path: Path,
    limits: _Limits,
    callback: Callable[[object], None],
    *,
    snapshot: STLSourceSnapshot | None = None,
) -> _PassStats:
    import numpy as np

    lower = np.full(3, np.inf, dtype=np.float64)
    upper = np.full(3, -np.inf, dtype=np.float64)
    parsed = 0
    source = snapshot if snapshot is not None else snapshot_stl(path)
    for vertices in iter_stl_blocks(path, _reader_limits(limits), snapshot=source):
        # Preserve source precision until the relative visual projection.
        callback(vertices)
        lower = np.minimum(lower, vertices.min(axis=(0, 1)))
        upper = np.maximum(upper, vertices.max(axis=(0, 1)))
        parsed += len(vertices)
    return _PassStats(
        source,
        parsed,
        source.size,
        (float(lower[0]), float(lower[1]), float(lower[2])),
        (float(upper[0]), float(upper[1]), float(upper[2])),
    )


def _reuse_measurements(
    measured: STLMeasurements, source: STLSourceSnapshot, limits: _Limits
) -> _PassStats:
    """Reuse only a complete scan of the current source within preview budgets."""
    _check_deadline(limits)
    if not isinstance(measured, STLMeasurements):
        raise _InvalidSTL("invalid source measurements")
    if measured.snapshot != source:
        raise STLSourceChanged("source changed after measurement")
    if (
        type(measured.triangle_count) is not int
        or measured.triangle_count <= 0
        or type(measured.scanned_bytes) is not int
        or measured.scanned_bytes != source.size
    ):
        raise _InvalidSTL("invalid measured source counts")
    if measured.triangle_count > limits.max_triangles:
        raise _BudgetExceeded("triangle budget")
    if measured.scanned_bytes > limits.max_source_bytes:
        raise _BudgetExceeded("source budget")
    if (
        type(measured.bounds_min) is not tuple
        or type(measured.bounds_max) is not tuple
        or len(measured.bounds_min) != 3
        or len(measured.bounds_max) != 3
    ):
        raise _InvalidSTL("invalid measured source bounds")
    for lower, upper in zip(measured.bounds_min, measured.bounds_max, strict=True):
        if (
            type(lower) not in (int, float)
            or type(upper) not in (int, float)
            or not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower > upper
        ):
            raise _InvalidSTL("invalid measured source bounds")
    return _PassStats(
        source,
        measured.triangle_count,
        measured.scanned_bytes,
        measured.bounds_min,
        measured.bounds_max,
    )


def _frame(
    bounds_min: tuple[float, float, float],
    bounds_max: tuple[float, float, float],
):
    from itertools import product

    import numpy as np

    from app.modules.media.mesh_render import _select_view_rotation

    exact_min = np.asarray(bounds_min, dtype=np.float64)
    exact_max = np.asarray(bounds_max, dtype=np.float64)
    center = exact_min + (exact_max - exact_min) * 0.5
    corners = np.asarray(
        list(product(*zip(exact_min, exact_max, strict=True))), dtype=np.float64
    )
    rotation = _select_view_rotation(corners - center, np)
    view_corners = (corners - center) @ rotation.T
    extent_x = max(float(np.ptp(view_corners[:, 0])), 1e-6)
    extent_y = max(float(np.ptp(view_corners[:, 1])), 1e-6)
    projected_mid = (view_corners.max(axis=0) + view_corners.min(axis=0)) * 0.5
    return center, rotation, projected_mid, extent_x, extent_y


def _nondegenerate_triangles(
    view: NDArray[np.float32] | NDArray[np.float64],
) -> NDArray[np.bool_]:
    """Classify source facets by differences between their three vertices."""
    import numpy as np

    raw = np.cross(view[:, 1] - view[:, 0], view[:, 2] - view[:, 0])
    length = np.linalg.norm(raw, axis=1)
    return np.isfinite(length) & (length > 1e-12)


def _render(
    path: Path,
    output: Path,
    width: int,
    height: int,
    limits: _Limits,
    first: _PassStats,
) -> int:
    import io

    import numpy as np
    from PIL import Image

    from app.modules.media.mesh_render import RasterBudget, _rasterise_triangles

    # Keep the raster canvas at the requested output size.  The old half-size
    # canvas made the stored preview a two-times enlargement of a coarse,
    # stippled image; these buffers are bounded by the thumbnail dimensions and
    # are tiny compared with the source mesh.
    coverage_width = max(1, width)
    coverage_height = max(1, height)
    (
        center,
        rotation,
        projected_mid,
        extent_x,
        extent_y,
    ) = _frame(first.bounds_min, first.bounds_max)
    # Every finite source facet contributes to the camera bounds. The standard
    # margin accommodates the complete scene, including small remote components.
    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

    margin = PREVIEW_PROFILE.margin_fraction
    scale = min(
        coverage_width * (1.0 - 2 * margin) / extent_x,
        coverage_height * (1.0 - 2 * margin) / extent_y,
    )
    image = np.zeros((coverage_height, coverage_width, 3), dtype=np.uint8)
    zbuffer = np.full((coverage_height, coverage_width), np.inf, dtype=np.float32)
    raster_budget = RasterBudget(limit=limits.max_candidates)
    # The rasteriser still receives a valid normal callback while it fills the
    # depth buffer, but face normals are deliberately not used for colour.  A
    # dense STL often contains millions of tiny, differently-oriented facets;
    # lighting those normals directly is the source of the visible salt-and-
    # pepper pattern.  Colour is reconstructed from the final screen-space
    # depth field below, which is bounded by the thumbnail dimensions.
    base_color = np.asarray([255, 255, 255], dtype=np.float32)

    def shade(normals):
        return np.ones_like(normals, dtype=np.float32)

    rendered = 0

    def draw(vertices) -> None:
        nonlocal rendered
        _check_deadline(limits)
        import numpy as np

        tri = np.asarray(vertices, dtype=np.float64)
        # Source coordinates remain float64 until translation is removed.
        view = (tri - center) @ rotation.T
        # Apply the bounded image scale before converting projected coordinates.
        # Finite source values near float32 max can overflow after rotation.
        screen = np.empty(view.shape, dtype=np.float32)
        screen[:, :, 0] = (
            view[:, :, 0] - float(projected_mid[0])
        ) * scale + coverage_width * 0.5
        screen[:, :, 1] = (
            coverage_height * 0.5 - (view[:, :, 1] - float(projected_mid[1])) * scale
        )
        screen[:, :, 2] = view[:, :, 2] * scale
        valid = np.isfinite(screen).all(axis=(1, 2))
        if not valid.all():
            raise _InvalidSTL("non-finite projection")
        valid_normal = _nondegenerate_triangles(view[valid])
        if not valid_normal.any():
            return
        screen = screen[valid_normal]
        # Keep the degeneracy check (it prevents malformed facets from
        # consuming raster candidates), but use a neutral normal here.  The
        # depth pass is shaded in screen space after all chunks have resolved
        # into the z-buffer, so no microfacet normal can leak into the image.
        corner_normals = np.zeros((int(valid_normal.sum()), 3, 3), dtype=np.float32)
        corner_normals[:, :, 2] = 1.0
        before = raster_budget.used
        _rasterise_triangles(
            image,
            zbuffer,
            screen,
            corner_normals,
            shade,
            base_color,
            coverage_width,
            coverage_height,
            budget=raster_budget,
        )
        rendered += 1
        if raster_budget.used > limits.max_candidates or (
            raster_budget.used == limits.max_candidates
            and before < limits.max_candidates
        ):
            raise _BudgetExceeded("candidate budget")

    second = _read_pass(path, limits, draw, snapshot=first.snapshot)
    if second.triangle_count != first.triangle_count:
        raise _InvalidSTL("source changed between passes")
    if not np.allclose(first.bounds_min, second.bounds_min, rtol=0, atol=0):
        raise _InvalidSTL("source changed between passes")
    if not np.allclose(first.bounds_max, second.bounds_max, rtol=0, atol=0):
        raise _InvalidSTL("source changed between passes")
    finite = np.isfinite(zbuffer)
    if not finite.any() or rendered == 0:
        raise _InvalidSTL("no visible triangles")

    # Reconstruct a smooth normal field from neighbouring depth samples.  The
    # arrays below are all fixed to the thumbnail dimensions (never triangle
    # count), and invalid neighbours are ignored so a real hole remains
    # transparent instead of being filled by post-processing.
    safe_depth = np.where(finite, zbuffer, 0.0)
    left = np.roll(safe_depth, 1, axis=1)
    right = np.roll(safe_depth, -1, axis=1)
    left_ok = np.roll(finite, 1, axis=1)
    right_ok = np.roll(finite, -1, axis=1)
    left_ok[:, 0] = False
    right_ok[:, -1] = False
    both_x = left_ok & right_ok
    dz_dx = np.where(
        both_x,
        (right - left) * 0.5,
        np.where(
            right_ok, right - safe_depth, np.where(left_ok, safe_depth - left, 0.0)
        ),
    )
    # Release the horizontal neighbours before allocating the vertical set;
    # at the maximum supported thumbnail dimension this saves several dozen
    # megabytes of simultaneously-live fixed-size arrays.
    del left, right, left_ok, right_ok, both_x

    up = np.roll(safe_depth, 1, axis=0)
    down = np.roll(safe_depth, -1, axis=0)
    up_ok = np.roll(finite, 1, axis=0)
    down_ok = np.roll(finite, -1, axis=0)
    up_ok[0, :] = False
    down_ok[-1, :] = False
    both_y = up_ok & down_ok
    dz_drow = np.where(
        both_y,
        (down - up) * 0.5,
        np.where(down_ok, down - safe_depth, np.where(up_ok, safe_depth - up, 0.0)),
    )
    # Depth is scaled into screen units before float32 conversion. Rows grow
    # downwards, hence the sign on Y for the view-space normal.
    slope_x = np.clip(dz_dx, -8.0, 8.0)
    slope_y = np.clip(dz_drow, -8.0, 8.0)
    normals = np.stack((-slope_x, slope_y, np.ones_like(slope_x)), axis=-1)
    normal_length = np.linalg.norm(normals, axis=2, keepdims=True)
    normals /= np.maximum(normal_length, 1e-6)
    del (
        safe_depth,
        up,
        down,
        up_ok,
        down_ok,
        both_y,
        dz_dx,
        dz_drow,
        slope_x,
        slope_y,
        normal_length,
    )

    # A single 3x3 normalized box pass removes residual one-pixel depth noise
    # without touching transparent pixels.  Accumulate one component at a time
    # so temporary memory stays O(width*height), independent of triangle count.
    smoothed = np.zeros_like(normals)
    support = np.zeros((coverage_height, coverage_width), dtype=np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted_valid = np.roll(finite, (dy, dx), axis=(0, 1))
            if dy < 0:
                shifted_valid[dy:, :] = False
            elif dy > 0:
                shifted_valid[:dy, :] = False
            if dx < 0:
                shifted_valid[:, dx:] = False
            elif dx > 0:
                shifted_valid[:, :dx] = False
            support += shifted_valid
            shifted_normals = np.roll(normals, (dy, dx), axis=(0, 1))
            for component in range(3):
                smoothed[:, :, component] += np.where(
                    shifted_valid, shifted_normals[:, :, component], 0.0
                )
    normals = smoothed / np.maximum(support[:, :, None], 1.0)
    normal_length = np.linalg.norm(normals, axis=2, keepdims=True)
    normals /= np.maximum(normal_length, 1e-6)

    light = np.asarray([-0.45, 0.6, 1.0], dtype=np.float32)
    light /= np.linalg.norm(light)
    diffuse = np.clip(normals @ light, 0.0, 1.0)
    brightness = 0.30 + diffuse * 0.70
    # A cool blue-grey surface provides contrast against the light card while
    # retaining enough range for the reconstructed depth shading to read.
    albedo = np.asarray(PREVIEW_PROFILE.material_albedo, dtype=np.float32) * 255.0
    rim = (1.0 - np.clip(normals[:, :, 2], 0.0, 1.0)) ** 2 * 18.0
    shaded = np.clip(
        albedo[None, None, :] * brightness[:, :, None] + rim[:, :, None], 0, 255
    )
    image[finite] = shaded[finite].astype(np.uint8)
    alpha = np.where(finite, 255, 0).astype(np.uint8)
    rgb = np.asarray(image, dtype=np.uint8)
    alpha = np.asarray(
        Image.fromarray(alpha, mode="L").resize(
            (width, height), Image.Resampling.LANCZOS
        ),
        dtype=np.uint8,
    )
    rgba = Image.fromarray(np.dstack((rgb, alpha)), mode="RGBA")
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) > 8 * 1024 * 1024:
        raise _BudgetExceeded("output budget")
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, output)
    return raster_budget.used


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def main(
    argv: list[str] | None = None,
    *,
    apply_limits: bool = True,
    measurements: STLMeasurements | None = None,
) -> int:
    if measurements is not None and apply_limits:
        # A hint never crosses the subprocess wire. The outer native worker
        # already owns containment before requesting in-process scan reuse.
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("width", type=int)
    parser.add_argument("height", type=int)
    parser.add_argument("--max-triangles", type=int, required=True)
    parser.add_argument("--max-source-bytes", type=int, required=True)
    parser.add_argument("--max-candidates", type=int, required=True)
    parser.add_argument("--chunk-triangles", type=int, required=True)
    parser.add_argument("--max-lines", type=int, required=True)
    parser.add_argument("--max-line-bytes", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--address-space-bytes", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--expected-parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    if not (
        1 <= args.width <= _MAX_RENDER_DIMENSION
        and 1 <= args.height <= _MAX_RENDER_DIMENSION
    ):
        return 2
    if (
        min(
            args.max_triangles,
            args.max_source_bytes,
            args.max_candidates,
            args.chunk_triangles,
            args.max_lines,
            args.max_line_bytes,
        )
        <= 0
        or args.max_triangles > _MAX_TRIANGLES
        or args.max_source_bytes > _MAX_SOURCE_BYTES
        or args.max_candidates > _MAX_CANDIDATES
        or args.chunk_triangles > _MAX_CHUNK_TRIANGLES
        or args.max_lines > _MAX_LINES
        or args.max_line_bytes > _MAX_LINE_BYTES
        or not math.isfinite(args.timeout_seconds)
        or args.timeout_seconds <= 0
        or args.timeout_seconds > _MAX_TIMEOUT_SECONDS
        or args.address_space_bytes <= 0
        or args.address_space_bytes > _MAX_ADDRESS_SPACE_BYTES
        or args.cpu_seconds <= 0
        or args.expected_parent_pid < 1
    ):
        return 2
    try:
        if apply_limits:
            _apply_worker_limits(
                args.address_space_bytes,
                args.cpu_seconds,
                expected_parent_pid=args.expected_parent_pid,
            )
    except _InvalidSTL:
        return 3
    limits = _Limits(
        max_triangles=args.max_triangles,
        max_source_bytes=args.max_source_bytes,
        max_candidates=args.max_candidates,
        chunk_triangles=args.chunk_triangles,
        max_lines=args.max_lines,
        max_line_bytes=args.max_line_bytes,
        deadline=time.monotonic() + args.timeout_seconds,
    )
    try:
        source = snapshot_stl(args.source)
        if source.size > limits.max_source_bytes:
            raise _BudgetExceeded("source budget")
        first = (
            _reuse_measurements(measurements, source, limits)
            if measurements is not None
            else _read_pass(
                args.source, limits, lambda _vertices: None, snapshot=source
            )
        )
        if first.triangle_count > limits.max_triangles:
            raise _BudgetExceeded("triangle budget")
        candidates = _render(
            args.source,
            args.output,
            args.width,
            args.height,
            limits,
            first,
        )
        if snapshot_stl(args.source) != source:
            raise STLSourceChanged("source changed before publication")
        _write_manifest(
            args.manifest,
            {
                "version": _WORKER_VERSION,
                "status": "complete",
                "width": args.width,
                "height": args.height,
                "triangle_count": first.triangle_count,
                "parsed_triangles": first.triangle_count,
                "scanned_bytes": first.scanned_bytes,
                "raster_candidates": candidates,
                "bounds_min": list(first.bounds_min),
                "bounds_max": list(first.bounds_max),
            },
        )
        return 0
    except (_InvalidSTL, OSError, ValueError, struct.error):
        return 3
    except MemoryError:
        raise
    except Exception:
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

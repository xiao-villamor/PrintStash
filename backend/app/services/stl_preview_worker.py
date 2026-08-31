"""Disposable two-pass STL thumbnail worker.

This module intentionally has no FastAPI or database dependencies.  The parent
process supplies all budgets on the command line and accepts output only when
the worker exits successfully and writes a complete manifest.  A pass keeps
only bounded chunk arrays and a tiny deterministic framing reservoir.
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
from typing import Callable, Iterator

_BINARY_HEADER_BYTES = 84
_BINARY_RECORD_BYTES = 50
_FLOAT32_MAX = 3.4028234663852886e38
_WORKER_VERSION = 1
_RESERVOIR_SIZE = 4096
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


class _InvalidSTL(Exception):
    pass


class _BudgetExceeded(_InvalidSTL):
    pass


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
    triangle_count: int
    scanned_bytes: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]


class _FramingReservoir:
    """Deterministic bounded reservoir of triangle centroids."""

    def __init__(self) -> None:
        self.values: list[tuple[float, float, float]] = []
        self.seen = 0
        self._state = 0x9E3779B9

    def add(self, centers) -> None:
        for center in centers:
            self.seen += 1
            value = (float(center[0]), float(center[1]), float(center[2]))
            if len(self.values) < _RESERVOIR_SIZE:
                self.values.append(value)
                continue
            # Deterministic LCG instead of the process-global random module.
            self._state = (1664525 * self._state + 1013904223) & 0xFFFFFFFF
            index = self._state % self.seen
            if index < _RESERVOIR_SIZE:
                self.values[index] = value


def _check_deadline(limits: _Limits) -> None:
    if time.monotonic() >= limits.deadline:
        raise _BudgetExceeded("deadline")


def _valid_value(value: float) -> bool:
    return math.isfinite(value) and abs(value) <= _FLOAT32_MAX


def _source_is_binary(path: Path) -> tuple[int, int] | None:
    try:
        size = path.stat().st_size
        if size < _BINARY_HEADER_BYTES:
            return None
        with path.open("rb") as stream:
            header = stream.read(_BINARY_HEADER_BYTES)
        if len(header) != _BINARY_HEADER_BYTES:
            return None
        count = struct.unpack_from("<I", header, 80)[0]
        if count <= 0 or count > 20_000_000:
            return None
        if size != _BINARY_HEADER_BYTES + count * _BINARY_RECORD_BYTES:
            return None
        return count, size
    except (OSError, struct.error):
        return None


def _read_binary(
    path: Path,
    limits: _Limits,
    callback: Callable[[object], None],
) -> _PassStats:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise _InvalidSTL("numpy unavailable") from exc

    info = _source_is_binary(path)
    if info is None:
        raise _InvalidSTL("not an exact binary STL")
    declared, size = info
    if declared > limits.max_triangles or size > limits.max_source_bytes:
        raise _BudgetExceeded("source budget")
    dtype = np.dtype(
        [
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ],
        align=False,
    )
    lower = np.full(3, np.inf, dtype=np.float64)
    upper = np.full(3, -np.inf, dtype=np.float64)
    scanned = _BINARY_HEADER_BYTES
    with path.open("rb") as stream:
        header = stream.read(_BINARY_HEADER_BYTES)
        if len(header) != _BINARY_HEADER_BYTES:
            raise _InvalidSTL("truncated header")
        parsed = 0
        while parsed < declared:
            _check_deadline(limits)
            count = min(max(limits.chunk_triangles, 1), declared - parsed)
            raw = stream.read(count * _BINARY_RECORD_BYTES)
            scanned += len(raw)
            if len(raw) != count * _BINARY_RECORD_BYTES:
                raise _InvalidSTL("truncated record")
            records = np.frombuffer(raw, dtype=dtype, count=count)
            vertices = records["vertices"]
            if not np.isfinite(vertices).all():
                raise _InvalidSTL("non-finite coordinate")
            callback(vertices)
            lower = np.minimum(lower, vertices.min(axis=(0, 1)))
            upper = np.maximum(upper, vertices.max(axis=(0, 1)))
            parsed += count
    return _PassStats(
        triangle_count=parsed,
        scanned_bytes=scanned,
        bounds_min=(float(lower[0]), float(lower[1]), float(lower[2])),
        bounds_max=(float(upper[0]), float(upper[1]), float(upper[2])),
    )


def _parse_float(token: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise _InvalidSTL("invalid number") from exc
    if not _valid_value(value):
        raise _InvalidSTL("non-finite coordinate")
    return value


def _iter_ascii_facets(
    stream, limits: _Limits
) -> Iterator[tuple[tuple[float, float, float], ...]]:
    state = "outside"
    vertices: list[tuple[float, float, float]] = []
    lines = 0
    scanned = 0
    saw_facet = False
    ended = False
    while True:
        _check_deadline(limits)
        if lines >= limits.max_lines:
            raise _BudgetExceeded("line budget")
        raw = stream.readline(limits.max_line_bytes + 1)
        if not raw:
            break
        scanned += len(raw)
        lines += 1
        if scanned > limits.max_source_bytes:
            raise _BudgetExceeded("source budget")
        if len(raw) > limits.max_line_bytes:
            raise _InvalidSTL("line too long")
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise _InvalidSTL("non-ascii input") from exc
        if not text or text.startswith("#") or text.startswith("//"):
            continue
        parts = text.split()
        keyword = parts[0].lower()
        if state == "outside":
            if ended:
                raise _InvalidSTL("content after endsolid")
            if keyword == "solid":
                continue
            if keyword == "facet":
                if len(parts) != 5 or parts[1].lower() != "normal":
                    raise _InvalidSTL("invalid facet normal")
                for token in parts[2:]:
                    _parse_float(token)
                state = "facet"
                saw_facet = True
                continue
            if keyword == "endsolid":
                ended = True
                continue
            raise _InvalidSTL("unexpected ASCII STL token")
        if state == "facet":
            if keyword != "outer" or len(parts) != 2 or parts[1].lower() != "loop":
                raise _InvalidSTL("missing outer loop")
            vertices = []
            state = "loop"
            continue
        if state == "loop":
            if keyword != "vertex" or len(parts) != 4:
                raise _InvalidSTL("invalid vertex")
            vertices.append(
                (
                    _parse_float(parts[1]),
                    _parse_float(parts[2]),
                    _parse_float(parts[3]),
                )
            )
            if len(vertices) > 3:
                raise _InvalidSTL("too many vertices")
            if len(vertices) == 3:
                state = "endloop"
            continue
        if state == "endloop":
            if keyword != "endloop" or len(parts) != 1:
                raise _InvalidSTL("missing endloop")
            state = "endfacet"
            continue
        if state == "endfacet":
            if keyword != "endfacet" or len(parts) != 1:
                raise _InvalidSTL("missing endfacet")
            yield (vertices[0], vertices[1], vertices[2])
            vertices = []
            state = "outside"
            continue
    # ``endsolid`` is required by the formal grammar but a number of slicers
    # omit it while still emitting complete facets. EOF at a facet boundary is
    # unambiguous and safe to accept; an unfinished loop/facet remains invalid.
    if state != "outside" or not saw_facet:
        raise _InvalidSTL("truncated ASCII STL")


def _read_ascii(
    path: Path,
    limits: _Limits,
    callback: Callable[[object], None],
) -> _PassStats:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise _InvalidSTL("numpy unavailable") from exc
    lower = np.full(3, np.inf, dtype=np.float64)
    upper = np.full(3, -np.inf, dtype=np.float64)
    parsed = 0
    batch: list[tuple[tuple[float, float, float], ...]] = []
    with path.open("rb") as stream:
        for facet in _iter_ascii_facets(stream, limits):
            _check_deadline(limits)
            if parsed >= limits.max_triangles:
                raise _BudgetExceeded("triangle budget")
            batch.append(facet)
            vertices = np.asarray(facet, dtype=np.float32)
            lower = np.minimum(lower, vertices.min(axis=0))
            upper = np.maximum(upper, vertices.max(axis=0))
            parsed += 1
            if len(batch) >= max(limits.chunk_triangles, 1):
                chunk = np.asarray(batch, dtype=np.float32)
                if not np.isfinite(chunk).all():
                    raise _InvalidSTL("non-finite coordinate")
                callback(chunk)
                batch.clear()
        if batch:
            chunk = np.asarray(batch, dtype=np.float32)
            if not np.isfinite(chunk).all():
                raise _InvalidSTL("non-finite coordinate")
            callback(chunk)
    if parsed == 0:
        raise _InvalidSTL("empty ASCII STL")
    return _PassStats(
        triangle_count=parsed,
        scanned_bytes=int(path.stat().st_size),
        bounds_min=(float(lower[0]), float(lower[1]), float(lower[2])),
        bounds_max=(float(upper[0]), float(upper[1]), float(upper[2])),
    )


def _read_pass(
    path: Path,
    limits: _Limits,
    callback: Callable[[object], None],
) -> _PassStats:
    if _source_is_binary(path) is not None:
        return _read_binary(path, limits, callback)
    return _read_ascii(path, limits, callback)


def _frame(
    bounds_min: tuple[float, float, float],
    bounds_max: tuple[float, float, float],
    reservoir: _FramingReservoir,
):
    from itertools import product

    import numpy as np

    from app.services.mesh_render import _select_view_rotation

    sampled = np.asarray(reservoir.values, dtype=np.float64)
    exact_min = np.asarray(bounds_min, dtype=np.float64)
    exact_max = np.asarray(bounds_max, dtype=np.float64)
    if sampled.ndim != 2 or sampled.shape[0] == 0:
        sampled = np.asarray([exact_min, exact_max], dtype=np.float64)
    # Percentiles reject a tiny number of finite outliers while preserving the
    # exact bounds separately for metadata.
    robust_min = np.percentile(sampled, 0.5, axis=0)
    robust_max = np.percentile(sampled, 99.5, axis=0)
    exact_extent = exact_max - exact_min
    robust_extent = robust_max - robust_min
    for axis in range(3):
        if robust_extent[axis] <= max(exact_extent[axis] * 0.01, 1e-9):
            robust_min[axis] = exact_min[axis]
            robust_max[axis] = exact_max[axis]
    center = (robust_min + robust_max) * 0.5
    corners = np.asarray(list(product(*zip(robust_min, robust_max, strict=True))))
    rotation = _select_view_rotation(corners - center, np)
    view_corners = (corners - center) @ rotation.T
    extent_x = max(float(np.ptp(view_corners[:, 0])), 1e-6)
    extent_y = max(float(np.ptp(view_corners[:, 1])), 1e-6)
    projected_mid = (view_corners.max(axis=0) + view_corners.min(axis=0)) * 0.5
    return (
        center.astype(np.float32),
        rotation.astype(np.float32),
        robust_min,
        robust_max,
        projected_mid.astype(np.float32),
        extent_x,
        extent_y,
    )


def _render(
    path: Path,
    output: Path,
    width: int,
    height: int,
    limits: _Limits,
    first: _PassStats,
    reservoir: _FramingReservoir,
) -> int:
    import io

    import numpy as np
    from PIL import Image

    from app.services.mesh_render import RasterBudget, _rasterise_triangles

    # Keep the raster canvas at the requested output size.  The old half-size
    # canvas made the stored preview a two-times enlargement of a coarse,
    # stippled image; these buffers are bounded by the thumbnail dimensions and
    # are tiny compared with the source mesh.
    coverage_width = max(1, width)
    coverage_height = max(1, height)
    (
        center,
        rotation,
        robust_min,
        robust_max,
        projected_mid,
        extent_x,
        extent_y,
    ) = _frame(first.bounds_min, first.bounds_max, reservoir)
    # The robust bounds already discard only the extreme centroid outliers and
    # the render bounds below retain a 5% safety expansion.  A 10% frame margin
    # gives small previews useful scale without clipping normal STL geometry.
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

    robust_span = np.maximum(robust_max - robust_min, 1e-6)
    # A triangle with an extreme vertex can otherwise expand to the whole frame
    # and consume the candidate budget. Such facets are omitted from framing,
    # while the exact source bounds remain available in the manifest.
    expanded_min = robust_min - robust_span * 0.05
    expanded_max = robust_max + robust_span * 0.05
    rendered = 0

    def draw(vertices) -> None:
        nonlocal rendered
        _check_deadline(limits)
        import numpy as np

        tri = np.asarray(vertices, dtype=np.float32)
        view = (tri - center) @ rotation.T
        screen = np.empty_like(view)
        screen[:, :, 0] = (
            view[:, :, 0] - float(projected_mid[0])
        ) * scale + coverage_width * 0.5
        screen[:, :, 1] = (
            coverage_height * 0.5 - (view[:, :, 1] - float(projected_mid[1])) * scale
        )
        screen[:, :, 2] = view[:, :, 2]
        valid = np.isfinite(screen).all(axis=(1, 2))
        valid &= (tri >= expanded_min).all(axis=(1, 2)) & (tri <= expanded_max).all(
            axis=(1, 2)
        )
        if not valid.any():
            return
        tri = tri[valid]
        screen = screen[valid]
        raw = np.cross(
            view[valid, :, 1] - view[valid, :, 0], view[valid, :, 2] - view[valid, :, 0]
        )
        length = np.linalg.norm(raw, axis=1)
        valid_normal = np.isfinite(length) & (length > 1e-12)
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

    second = _read_pass(path, limits, draw)
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
    # One screen pixel is 1/scale model units.  Screen rows grow downwards,
    # hence the sign on the Y component for the view-space normal.
    slope_x = np.clip(dz_dx * scale, -8.0, 8.0)
    slope_y = np.clip(dz_drow * scale, -8.0, 8.0)
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
    albedo = np.asarray([104.0, 130.0, 166.0], dtype=np.float32)
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


def main(argv: list[str] | None = None) -> int:
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
        source_stat = args.source.stat()
        if source_stat.st_size > limits.max_source_bytes:
            raise _BudgetExceeded("source budget")
        reservoir = _FramingReservoir()

        def collect(vertices) -> None:
            reservoir.add(vertices.mean(axis=1))

        first = _read_pass(args.source, limits, collect)
        if first.triangle_count > limits.max_triangles:
            raise _BudgetExceeded("triangle budget")
        first_after = args.source.stat()
        if (
            first_after.st_size != source_stat.st_size
            or first_after.st_mtime_ns != source_stat.st_mtime_ns
        ):
            raise _InvalidSTL("source changed during first pass")
        before = first_after
        candidates = _render(
            args.source,
            args.output,
            args.width,
            args.height,
            limits,
            first,
            reservoir,
        )
        after = args.source.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise _InvalidSTL("source changed during render")
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
    except Exception:
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

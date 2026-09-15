"""Bounded-memory STL thumbnail fallback used when full mesh loading is unsafe."""

from __future__ import annotations

import math
import struct
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE


@dataclass(frozen=True)
class STLThumbnailResult:
    png: bytes
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    triangle_count: int
    sampled_triangles: int
    scanned_bytes: int = 0
    parsed_triangles: int = 0
    complete: bool = True
    raster_candidates: int = 0


@dataclass
class _SampledSTL:
    """A bounded sample and the work spent obtaining it."""

    coordinates: array
    triangle_count: int
    sampled_triangles: int
    bounds_min: tuple[float, float, float] | None
    bounds_max: tuple[float, float, float] | None
    scanned_bytes: int
    parsed_triangles: int
    complete: bool


_BINARY_HEADER_BYTES = 84
_BINARY_TRIANGLE = struct.Struct("<12fH")
# This is a hard facet-work budget. Binary files are sampled by deterministic
# seeks and ASCII files stop after this many parsed facets. Neither path gets
# a second pass over the source.
_MAX_SAMPLED_TRIANGLES = 100_000
_COVERAGE_CHUNK_TRIANGLES = 2_048
_MAX_ASCII_LINE_BYTES = 64 * 1024
_MAX_ASCII_BYTES = 16 * 1024 * 1024
_MAX_ASCII_LINES = 1_000_000
_FLOAT32_MAX = 3.4028234663852886e38
_MAX_RENDER_DIMENSION = 2048
_MAX_COVERAGE_CANDIDATES = 2_000_000
# Keep the footprint deliberately small: a sparse bounded sample of a
# microfaceted surface needs coverage, but must not turn the fallback into a
# silhouette mask that erases meaningful holes.
_MIN_SPLAT_RADIUS = 0.65
_MAX_SPLAT_RADIUS = 1.0


def _binary_stl_info(path: Path) -> tuple[int, int] | None:
    """Return ``(declared facets, file size)`` for a valid binary STL."""

    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(_BINARY_HEADER_BYTES)
        if len(header) != _BINARY_HEADER_BYTES:
            return None
        count = struct.unpack("<I", header[80:84])[0]
        expected_size = _BINARY_HEADER_BYTES + count * _BINARY_TRIANGLE.size
        if count == 0 or size < expected_size:
            return None
        return count, size
    except (OSError, struct.error):
        return None


def _is_binary_stl(path: Path) -> bool:
    return _binary_stl_info(path) is not None


def _valid_coordinate(value: float) -> bool:
    """Return whether *value* is finite and representable in float32."""

    return math.isfinite(value) and abs(value) <= _FLOAT32_MAX


def _iter_binary_triangles(
    path: Path, *, max_triangles: int = _MAX_SAMPLED_TRIANGLES
) -> Iterator[tuple[float, ...]]:
    """Yield at most *max_triangles* records for compatibility/debugging."""

    try:
        with path.open("rb") as stream:
            header = stream.read(_BINARY_HEADER_BYTES)
            if len(header) != _BINARY_HEADER_BYTES:
                return
            count = struct.unpack("<I", header[80:84])[0]
            limit = min(max(max_triangles, 0), count)
            for _ in range(limit):
                record = stream.read(_BINARY_TRIANGLE.size)
                if len(record) != _BINARY_TRIANGLE.size:
                    return
                values = _BINARY_TRIANGLE.unpack(record)
                triangle = tuple(float(value) for value in values[3:12])
                if all(_valid_coordinate(value) for value in triangle):
                    yield triangle
    except OSError:
        return


def _iter_ascii_triangles(
    path: Path,
    *,
    max_bytes: int = _MAX_ASCII_BYTES,
    max_lines: int = _MAX_ASCII_LINES,
    max_triangles: int = _MAX_SAMPLED_TRIANGLES,
) -> Iterator[tuple[float, ...]]:
    """Yield ASCII facets while bounding bytes, lines, line size, and facets."""

    vertices: list[float] = []
    scanned_bytes = 0
    lines = 0
    parsed = 0
    draining = False
    try:
        with path.open("rb") as stream:
            while (
                scanned_bytes < max(max_bytes, 0)
                and lines < max(max_lines, 0)
                and parsed < max(max_triangles, 0)
            ):
                remaining = max_bytes - scanned_bytes
                read_limit = min(_MAX_ASCII_LINE_BYTES + 1, remaining)
                if read_limit <= 0:
                    return
                raw_line = stream.readline(read_limit)
                if not raw_line:
                    return
                scanned_bytes += len(raw_line)
                if draining:
                    if raw_line.endswith((b"\n", b"\r")):
                        draining = False
                    continue
                lines += 1
                if len(raw_line) > _MAX_ASCII_LINE_BYTES:
                    vertices.clear()
                    draining = not raw_line.endswith((b"\n", b"\r"))
                    continue
                line = raw_line.decode("ascii", errors="ignore")
                parts = line.lstrip().split()
                if len(parts) != 4 or parts[0].lower() != "vertex":
                    continue
                try:
                    values = [float(value) for value in parts[1:]]
                except ValueError:
                    vertices.clear()
                    continue
                if not all(_valid_coordinate(value) for value in values):
                    vertices.clear()
                    continue
                vertices.extend(values)
                if len(vertices) == 9:
                    parsed += 1
                    yield tuple(vertices)
                    vertices.clear()
    except OSError:
        return


def _iter_stl_triangles(
    path: Path, *, max_triangles: int = _MAX_SAMPLED_TRIANGLES
) -> Iterator[tuple[float, ...]]:
    if _is_binary_stl(path):
        yield from _iter_binary_triangles(path, max_triangles=max_triangles)
    else:
        yield from _iter_ascii_triangles(path, max_triangles=max_triangles)


def _update_bounds(
    lower: list[float], upper: list[float], triangle: tuple[float, ...]
) -> None:
    for offset in (0, 3, 6):
        for axis in range(3):
            value = triangle[offset + axis]
            lower[axis] = min(lower[axis], value)
            upper[axis] = max(upper[axis], value)


def _binary_sample_indices(count: int, sample_count: int) -> Iterator[int]:
    """Yield stratified records, retaining the first and last facet."""

    for sample_index in range(sample_count):
        if sample_index == 0:
            yield 0
            continue
        if sample_index == sample_count - 1:
            yield count - 1
            continue
        yield (sample_index * count + count // 2) // sample_count


def _read_binary_samples(
    path: Path, budget: int, info: tuple[int, int] | None = None
) -> _SampledSTL | None:
    from printstash_core.mesh.native_rasterizer import kernel

    if not 1 <= budget <= _MAX_SAMPLED_TRIANGLES:
        return None
    try:
        result = kernel().sample_binary_stl(path, budget)
    except OSError:
        return None
    if result is None:
        return None
    packed, count, parsed, lower, upper, scanned, complete = result
    coordinates = array("f")
    coordinates.frombytes(packed)
    return _SampledSTL(
        coordinates,
        count,
        parsed,
        tuple(lower),
        tuple(upper),
        scanned,
        parsed,
        complete,
    )


def _read_ascii_samples(
    path: Path, budget: int, probe_bytes: int = _BINARY_HEADER_BYTES
) -> _SampledSTL | None:
    coordinates = array("f")
    lower = [float("inf")] * 3
    upper = [float("-inf")] * 3
    vertices: list[float] = []
    scanned_bytes = probe_bytes
    lines = 0
    parsed = 0
    eof = False
    valid_source = True
    draining = False
    try:
        with path.open("rb") as stream:
            while (
                scanned_bytes < _MAX_ASCII_BYTES
                and lines < _MAX_ASCII_LINES
                and parsed < budget
            ):
                remaining = _MAX_ASCII_BYTES - scanned_bytes
                read_limit = min(_MAX_ASCII_LINE_BYTES + 1, remaining)
                if read_limit <= 0:
                    break
                raw_line = stream.readline(read_limit)
                if not raw_line:
                    eof = True
                    break
                scanned_bytes += len(raw_line)
                if draining:
                    if raw_line.endswith((b"\n", b"\r")):
                        draining = False
                    continue
                lines += 1
                if len(raw_line) > _MAX_ASCII_LINE_BYTES:
                    vertices.clear()
                    valid_source = False
                    draining = not raw_line.endswith((b"\n", b"\r"))
                    continue
                parts = raw_line.decode("ascii", errors="ignore").lstrip().split()
                if len(parts) != 4 or parts[0].lower() != "vertex":
                    continue
                try:
                    values = [float(value) for value in parts[1:]]
                except ValueError:
                    vertices.clear()
                    valid_source = False
                    continue
                if not all(_valid_coordinate(value) for value in values):
                    vertices.clear()
                    valid_source = False
                    continue
                vertices.extend(values)
                if len(vertices) == 9:
                    triangle = tuple(vertices)
                    coordinates.extend(triangle)
                    _update_bounds(lower, upper, triangle)
                    parsed += 1
                    vertices.clear()
    except OSError:
        return None
    if parsed == 0:
        return None
    return _SampledSTL(
        coordinates=coordinates,
        triangle_count=parsed,
        sampled_triangles=parsed,
        bounds_min=(lower[0], lower[1], lower[2]),
        bounds_max=(upper[0], upper[1], upper[2]),
        scanned_bytes=scanned_bytes,
        parsed_triangles=parsed,
        complete=eof and valid_source and not vertices and not draining,
    )


def _read_samples(path: Path, budget: int) -> _SampledSTL | None:
    info = _binary_stl_info(path)
    if info is not None:
        return _read_binary_samples(path, budget, info)
    try:
        probe_bytes = min(path.stat().st_size, _BINARY_HEADER_BYTES)
    except OSError:
        return None
    return _read_ascii_samples(path, budget, probe_bytes)


def sample_stl_geometry(
    path: Path, *, max_triangles: int = 10_000
) -> _SampledSTL | None:
    """The same bounded sampler used for previews, exposed for partial analysis.

    Sampled topology is never full topology. ``complete`` says the parser reached
    its source boundary, not that every source triangle is present in the sample.
    """
    if (
        type(max_triangles) is not int
        or not 1 <= max_triangles <= _MAX_SAMPLED_TRIANGLES
    ):
        raise ValueError("invalid_stl_sample_budget")
    return _read_samples(path, max_triangles)


def render_stl_thumbnail(
    path: Path,
    *,
    width: int = 640,
    height: int = 480,
    max_triangles: int | None = None,
) -> STLThumbnailResult | None:
    """Transport one bounded STL recovery job to the Rust preview engine."""
    from printstash_core.mesh.native_rasterizer import kernel

    if not (
        1 <= width <= _MAX_RENDER_DIMENSION and 1 <= height <= _MAX_RENDER_DIMENSION
    ):
        return None
    budget = min(
        _MAX_SAMPLED_TRIANGLES, max(1, max_triangles or _MAX_SAMPLED_TRIANGLES)
    )
    p = PREVIEW_PROFILE
    try:
        result = kernel().render_stl_fallback(
            path,
            width,
            height,
            budget,
            (
                p.margin_fraction,
                p.hero_azimuth_degrees,
                p.hero_elevation_degrees,
                p.flat_tilt_degrees,
                p.flat_thickness_ratio,
                *p.material_albedo,
            ),
        )
        image, count, parsed, lower, upper, scanned, complete, candidates, _seconds = (
            result
        )
        return STLThumbnailResult(
            png=image,
            bounds_min=tuple(lower),
            bounds_max=tuple(upper),
            triangle_count=count,
            sampled_triangles=parsed,
            scanned_bytes=scanned,
            parsed_triangles=parsed,
            complete=complete,
            raster_candidates=candidates,
        )
    except (AttributeError, OSError, ValueError, RuntimeError):
        return None

"""One memory policy for native admission, preflight and loader ceilings.

Weights combine a measured startup floor with historical whole-pipeline peak
costs per face. Those coefficients already include loader/render overhead;
adding the process baseline again would count it twice on small hosts. They are
scheduling estimates, never permission to exceed the hard worker limit.
Unknown source complexity claims the whole pool, so parallelism cannot turn an
unmeasurable input into an optimistic allocation.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.runtime.native_admission import Resources

# Cold NumPy + trimesh + scipy.spatial imports measured 293 MiB virtual
# memory / 112 MiB RSS on Python 3.14.8 Linux x86_64 with the current numeric
# stack (NumPy 2.5.3, SciPy 1.18.1, Trimesh 5.1.1). This floor leaves startup
# headroom for small jobs. The per-face coefficients below describe the whole
# pipeline peak, including allocator/render buffers, rather than marginal cost.
MIN_NATIVE_MEMORY = 512 * 1024**2
# Guarded 640x480 WEBP cube and full Benchy analysis measured virtual peaks
# of 548,909,056 and 746,389,504 bytes respectively on the same numeric stack.
MIN_WEBP_MEMORY = 640 * 1024**2
MIN_ANALYSIS_MEMORY = 1024**3
QUALIFIED_RASTER_PIXELS = 640 * 480
RASTER_PIXEL_BYTES = 64
FALLBACK_MEMORY = 2 * 1024**3
# A real 300k-facet STL measured ~754 MB peak virtual memory on this stack;
# the former 2200 B/face claim (660 MB) refused valid topology measurement.
# 3000 B/face leaves scheduling headroom for the bootstrap's hard AS ceiling.
DEFAULT_FACE_BYTES = 3000
# Guarded render-only WEBP Benchy (225706 faces) reached 714731520 virtual
# bytes. A 3000 B/face claim was smaller than that measured pipeline peak.
# This whole-pipeline coefficient includes the existing startup envelope.
WEBP_FACE_BYTES = 4000
THREE_MF_FACE_BYTES = 3600
# Optional text preflight must stay cheap in the supervisor. OBJ gets a
# separate CPU allowance and wall watchdog: runnable-thread contention must not
# turn a countable source into a whole-pool claim. Check at bounded 128-line
# intervals (at most 512 KiB); parsing still happens in a guarded child. Exhausting any
# bound keeps the original unknown-complexity claim; it never rejects a source.
TEXT_PREFLIGHT_BYTES = 8 * 1024**2
TEXT_PREFLIGHT_LINE_BYTES = 4096
TEXT_PREFLIGHT_SECONDS = 0.1
OBJ_PREFLIGHT_WALL_SECONDS = 1.0
TEXT_PREFLIGHT_CPU_SECONDS = 0.25


class RasterCodec(str, Enum):
    PNG = "png"
    WEBP = "webp"
    RGB = "rgb"


@dataclass(frozen=True)
class GeometryWork:
    """Load, measure or convert geometry without optional visual analysis."""


@dataclass(frozen=True)
class RasterWork:
    width: int
    height: int
    frames: int
    codec: RasterCodec

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (self.width, self.height, self.frames)
        ):
            raise ValueError(
                "raster dimensions and frame count must be positive integers"
            )
        if not isinstance(self.codec, RasterCodec):
            raise TypeError("raster codec must be a RasterCodec")


@dataclass(frozen=True)
class AnalysisWork:
    raster: RasterWork | None = None

    def __post_init__(self) -> None:
        if self.raster is not None and not isinstance(self.raster, RasterWork):
            raise TypeError("analysis raster must be a RasterWork")


WorkProfile = GeometryWork | RasterWork | AnalysisWork


def _work_memory(work: WorkProfile) -> tuple[int, int, int]:
    raster: RasterWork | None
    if isinstance(work, GeometryWork):
        minimum, raster = MIN_NATIVE_MEMORY, None
    elif isinstance(work, RasterWork):
        minimum, raster = (
            (MIN_WEBP_MEMORY if work.codec is RasterCodec.WEBP else MIN_NATIVE_MEMORY),
            work,
        )
    elif isinstance(work, AnalysisWork):
        minimum, raster = MIN_ANALYSIS_MEMORY, work.raster
    else:
        raise TypeError("native work must declare a typed profile")
    # Actual guarded WEBP and complete descriptor probes require more virtual
    # memory than cold import/geometry alone. These margins are whole-process
    # envelopes, not an additional copy of the startup baseline. The qualified
    # 640x480 frame already fits those envelopes; only larger or retained frames
    # add pixel workspace while source arrays remain live.
    incremental = (
        0
        if raster is None
        else max(
            0, raster.width * raster.height * raster.frames - QUALIFIED_RASTER_PIXELS
        )
        * RASTER_PIXEL_BYTES
    )
    face_bytes = (
        WEBP_FACE_BYTES
        if raster is not None and raster.codec is RasterCodec.WEBP
        else DEFAULT_FACE_BYTES
    )
    return minimum, incremental, face_bytes


@dataclass(frozen=True)
class MeshSource:
    path: Path
    file_type: str


def capacity(memory_limit: int | None, fraction: float, slots: int) -> Resources:
    if type(slots) is not int or slots < 0:
        raise ValueError("native slots must be a nonnegative integer")
    if (
        isinstance(fraction, bool)
        or not math.isfinite(fraction)
        or not 0 <= fraction <= 1
    ):
        raise ValueError("native memory fraction must be in [0, 1]")
    if memory_limit is not None and (
        type(memory_limit) is not int or memory_limit <= 0
    ):
        raise ValueError("detected memory must be a positive integer")
    # Zero disables geometry estimation, never process containment. Zero slots
    # retains the documented serial-execution configuration.
    total = (
        FALLBACK_MEMORY
        if memory_limit is None
        else max(1, int(memory_limit * (fraction or 0.5)))
    )
    return Resources(max(1, slots), total)


def request(
    pool: Resources,
    counts: tuple[int | None, ...],
    *,
    work: WorkProfile = GeometryWork(),
) -> Resources:
    if not counts:
        raise ValueError("native work must identify at least one source")
    if any(
        value is not None and (type(value) is not int or value < 0) for value in counts
    ):
        raise ValueError("face counts must be nonnegative integers or unknown")
    minimum, incremental, face_bytes = _work_memory(work)
    if any(value is None for value in counts):
        return Resources(1, pool.bytes)
    geometry = sum(value * face_bytes for value in counts if value is not None)
    return Resources(1, min(pool.bytes, max(minimum, geometry) + incremental))


def face_capacity(memory: int, suffix: str) -> int:
    if type(memory) is not int or memory <= 0:
        raise ValueError("admitted memory must be a positive integer")
    cost = THREE_MF_FACE_BYTES if suffix.lower() == ".3mf" else DEFAULT_FACE_BYTES
    return memory // cost


def _obj_triangle_count(path: Path, *, deadline: float) -> int | None:
    """Bound plain OBJ storage without parsing every coordinate twice.

    Admission counts records and polygon corners; the isolated source reader
    owns numeric and index validation. Each coordinate channel must fit the
    three-corners-per-triangle envelope. Materials, continuation syntax, and
    unknown directives keep the full-pool claim because they can add storage
    that these counts do not cover.
    """
    from app.modules.media.stl_reader import STLSourceSnapshot

    with path.open("rb") as stream:
        before = STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
        if before.size > TEXT_PREFLIGHT_BYTES:
            return None
        cpu_deadline = time.thread_time() + TEXT_PREFLIGHT_CPU_SECONDS
        coordinate_counts = [0, 0, 0]
        triangles = scanned = lines = 0
        while True:
            if lines % 128 == 0 and (
                time.monotonic() >= deadline or time.thread_time() >= cpu_deadline
            ):
                return None
            lines += 1
            raw = stream.readline(TEXT_PREFLIGHT_LINE_BYTES + 1)
            if not raw:
                break
            scanned += len(raw)
            if (
                len(raw) > TEXT_PREFLIGHT_LINE_BYTES
                or scanned > TEXT_PREFLIGHT_BYTES
                or not raw.isascii()
                or b"\\" in raw
                or b"\x00" in raw
                or b"mtllib" in raw
            ):
                return None
            fields = raw.split()
            if not fields or fields[0].startswith(b"#"):
                continue
            kind = fields[0]
            if kind in (b"v", b"vt", b"vn"):
                channel = {b"v": 0, b"vt": 1, b"vn": 2}[kind]
                if len(fields) not in ((3, 4) if channel == 1 else (4,)):
                    return None
                coordinate_counts[channel] += 1
            elif kind == b"f":
                if len(fields) < 4:
                    return None
                triangles += len(fields) - 3
            elif kind not in (b"o", b"g", b"s", b"l", b"p"):
                return None
        if (
            time.monotonic() >= deadline
            or time.thread_time() >= cpu_deadline
            or triangles == 0
            or any(count > 3 * triangles for count in coordinate_counts)
            or scanned != before.size
            or before != STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
            or before != STLSourceSnapshot.from_stat(path.stat())
        ):
            return None
        return triangles


def _ascii_triangle_bound(path: Path) -> int | None:
    """Bound accepted ASCII facets by physical lines, without decoding geometry.

    The canonical reader requires seven separate physical lines per facet:
    facet normal, outer loop, three vertices, endloop, endfacet. Blank lines,
    comments and solid delimiters can only increase this bound. This does not
    certify syntax: the guarded canonical reader still validates every token.
    Binary sources are recognized before this path; non-ASCII input stays
    unknown. Memory weights and the worker's hard ceilings remain unchanged.
    """
    from app.modules.media.stl_reader import (
        InvalidSTL,
        STLReadLimits,
        STLSourceSnapshot,
        _iter_ascii_facets,
    )

    deadline = time.monotonic() + OBJ_PREFLIGHT_WALL_SECONDS
    cpu_deadline = time.thread_time() + TEXT_PREFLIGHT_CPU_SECONDS
    with path.open("rb") as stream:
        before = STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
        if before.size > 1 << 30:
            return None
        # The legacy load guard interprets non-solid prefixes as binary-like.
        # Keep their full admission rather than giving that separate guard a
        # smaller ceiling which could turn invalid syntax into a resource error.
        if not stream.read(6).lower().startswith(b"solid"):
            return None
        stream.seek(0)
        try:
            next(
                _iter_ascii_facets(
                    stream,
                    STLReadLimits(
                        max_source_bytes=TEXT_PREFLIGHT_BYTES,
                        max_line_bytes=TEXT_PREFLIGHT_LINE_BYTES,
                        deadline=deadline,
                    ),
                )
            )
        except InvalidSTL, StopIteration:
            return None
        stream.seek(0)
        lines = consumed = 0
        terminated = True
        while block := stream.read(512 * 1024):
            consumed += len(block)
            if (
                consumed > before.size
                or not block.isascii()
                or b"\x00" in block
                or time.monotonic() >= deadline
                or time.thread_time() >= cpu_deadline
            ):
                return None
            lines += block.count(b"\n")
            terminated = block.endswith(b"\n")
        count = (lines + int(not terminated)) // 7
        if (
            count == 0
            or consumed != before.size
            or before != STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
            or before != STLSourceSnapshot.from_stat(path.stat())
        ):
            return None
        return count


def _source_triangle_count(source: MeshSource, *, minimum_faces: int = 0) -> int | None:
    """Conservative source counts for admission, never geometry certification."""
    from app.modules.media.stl_reader import binary_stl_info

    suffix = source.file_type.lower().lstrip(".")
    try:
        if suffix == "stl":
            from app.modules.media.stl_reader import STLSourceSnapshot

            with source.path.open("rb") as stream:
                before = STLSourceSnapshot.from_stat(os.fstat(stream.fileno()))
                info = binary_stl_info(source.path)
                byte_bound = before.size // 50
                # If even the densest source fits the startup floor, a full
                # text scan cannot reduce admission. The guarded loader still
                # owns syntax; the snapshot checks below still own identity.
                bound = (
                    info[0]
                    if info is not None
                    else byte_bound
                    if byte_bound <= minimum_faces
                    else _ascii_triangle_bound(source.path)
                )
                if bound is None:
                    # A binary facet occupies 50 bytes; even a minimally
                    # encoded ASCII facet needs more. Including header and
                    # trailing bytes keeps this an upper bound for both.
                    # It also dominates the loader's legacy binary-density
                    # guard, preserving malformed-input classification.
                    bound = before.size // 50
                if before != STLSourceSnapshot.from_stat(
                    os.fstat(stream.fileno())
                ) or before != STLSourceSnapshot.from_stat(source.path.stat()):
                    return None
                return bound
        if suffix == "obj":
            return _obj_triangle_count(
                source.path, deadline=time.monotonic() + OBJ_PREFLIGHT_WALL_SECONDS
            )
    except OSError, ValueError:
        # Unknown, changing or expensive text retains full isolation.
        return None
    return None


def estimate_sources(
    pool: Resources,
    sources: tuple[MeshSource, ...],
    *,
    work: WorkProfile = GeometryWork(),
) -> Resources:
    minimum, _incremental, face_bytes = _work_memory(work)
    # The floor applies to the combined request, not separately to each source.
    # Multi-source work needs actual bounds to retain its aggregate allowance.
    minimum_faces = minimum // face_bytes if len(sources) == 1 else 0
    return request(
        pool,
        tuple(
            _source_triangle_count(source, minimum_faces=minimum_faces)
            for source in sources
        ),
        work=work,
    )

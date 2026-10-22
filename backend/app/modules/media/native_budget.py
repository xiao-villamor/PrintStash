"""One memory policy for native admission, preflight and loader ceilings.

Weights combine a measured startup floor with historical whole-pipeline peak
costs per face. Those coefficients already include loader/render overhead;
adding the process baseline again would count it twice on small hosts. They are
scheduling estimates, never permission to exceed the hard worker limit.
Unknown source complexity claims the whole pool, so parallelism cannot turn an
unmeasurable input into an optimistic allocation. A 3MF package is countable
when its structure can be bounded without parsing it (`three_mf_bounds`).
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
# Whole-pipeline worker VmPeak for geometry plus a 640x480 thumbnail, measured
# over fifty real 3MF packages on the numeric stack above: a 391-440 MiB start,
# then at most 1940 bytes per placed face or 22 bytes per reached XML byte
# (attribute-heavy painted models). The claim keeps >= 20% headroom over every
# measured peak, and never less than the loader's own 3600 B/face ceiling.
THREE_MF_BASE_MEMORY = 512 * 1024**2
THREE_MF_PIPELINE_FACE_BYTES = 2000
THREE_MF_XML_BYTE_COST = 24
# The same pipeline with scenes read by lib3mf, over the packages it reads: at
# most 330 bytes per placed face or 4 per model XML byte above that start. The
# claim keeps >= 25% headroom, and the loader's ceilings use the same weight
# once the worker has read the scene through lib3mf.
THREE_MF_LIB3MF_FACE_BYTES = 600
THREE_MF_LIB3MF_XML_BYTE_COST = 6
# Counting structure streams each part once in the supervisor; inflating the
# part dominates (about 1.2 s CPU for 155 MB of model XML). Packages lib3mf
# reads claim a fraction of the pool even at 1.8M faces, so the scan is worth
# finishing for them.
THREE_MF_PREFLIGHT_WALL_SECONDS = 4.0
THREE_MF_PREFLIGHT_CPU_SECONDS = 2.0
# Whole-pipeline worker VmPeak for STEP over a mixed corpus: a 470 MiB start,
# then 570-850 bytes per tessellated face, so 1,500 keeps the loader's ceiling
# inside the permit.
STEP_FACE_BYTES = 1500
# Tessellation density is unknown until it runs. At 0.05 mm every part of that
# corpus peaked under 620 MiB, so a derivative first claims 1 GiB and runs
# once more under the whole pool if that is not enough.
STEP_OPTIMISTIC_MEMORY = 1024**3
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


def face_capacity(memory: int, suffix: str, *, lib3mf: bool = False) -> int:
    if type(memory) is not int or memory <= 0:
        raise ValueError("admitted memory must be a positive integer")
    if suffix.lower() in (".step", ".stp"):
        return memory // STEP_FACE_BYTES
    if suffix.lower() != ".3mf":
        return memory // DEFAULT_FACE_BYTES
    return memory // (THREE_MF_LIB3MF_FACE_BYTES if lib3mf else THREE_MF_FACE_BYTES)


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


def _three_mf_claim(
    pool: Resources, source: MeshSource, work: GeometryWork | RasterWork
) -> Resources | None:
    """Weight one package by its bounded structure, or ``None`` when unknown."""
    from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

    from app.core.native_settings import settings
    from app.modules.media.three_mf_bounds import bounds

    measured = bounds(
        source.path,
        face_cap=MAX_ANALYSIS_FACES,
        max_model_bytes=settings.mesh_3mf_max_xml_mb * 1024 * 1024,
        wall_seconds=THREE_MF_PREFLIGHT_WALL_SECONDS,
        cpu_seconds=THREE_MF_PREFLIGHT_CPU_SECONDS,
    )
    # A package beyond the reader's face ceiling is refused once it is read;
    # until then it is as unknown as one the scan could not bound.
    if measured is None or measured.placed_faces > MAX_ANALYSIS_FACES:
        return None
    minimum, incremental, face_bytes = _work_memory(work)
    faces = measured.placed_faces
    if measured.lib3mf_readable:
        claim = max(
            minimum,
            THREE_MF_BASE_MEMORY
            + max(
                faces * THREE_MF_LIB3MF_FACE_BYTES,
                measured.reached_model_bytes * THREE_MF_LIB3MF_XML_BYTE_COST,
            ),
        )
        return Resources(1, min(pool.bytes, claim + incremental))
    # The loader derives its face ceiling from the permit (`face_capacity`), so
    # the claim must admit every placed face at that weight as well.
    claim = max(
        minimum,
        faces * max(face_bytes, THREE_MF_FACE_BYTES),
        THREE_MF_BASE_MEMORY
        + max(
            faces * THREE_MF_PIPELINE_FACE_BYTES,
            measured.reached_model_bytes * THREE_MF_XML_BYTE_COST,
        ),
    )
    return Resources(1, min(pool.bytes, claim + incremental))


def optimistic_step_claim(pool: Resources, work: WorkProfile) -> Resources | None:
    """A STEP derivative's first claim, or ``None`` when it cannot be smaller.

    Only a caller that runs the work again with the whole pool on a memory
    refusal may ask for it.
    """
    if not isinstance(work, (GeometryWork, RasterWork)):
        return None
    minimum, incremental, _face_bytes = _work_memory(work)
    amount = max(minimum, STEP_OPTIMISTIC_MEMORY) + incremental
    return Resources(1, amount) if amount < pool.bytes else None


def _source_bytes(sources: tuple[MeshSource, ...]) -> int | None:
    try:
        return sum(source.path.stat().st_size for source in sources)
    except OSError:
        # A vanished source is the guarded reader's to report; telemetry
        # records that its size was unavailable rather than inventing one.
        return None


def estimate_sources(
    pool: Resources,
    sources: tuple[MeshSource, ...],
    *,
    work: WorkProfile = GeometryWork(),
) -> Resources:
    from app.modules.media.mesh_observability import record_estimate
    from app.modules.media.mesh_telemetry import EstimateBasis, EstimateStats

    started = time.monotonic_ns()
    minimum, _incremental, face_bytes = _work_memory(work)
    claim: Resources | None = None
    if (
        len(sources) == 1
        and sources[0].file_type.lower().lstrip(".") == "3mf"
        and isinstance(work, (GeometryWork, RasterWork))
    ):
        # Fingerprint analysis has its own uncalibrated peak and keeps the pool.
        claim = _three_mf_claim(pool, sources[0], work)
        counted = claim is not None
    if claim is None:
        # The floor applies to the combined request, not separately to each source.
        # Multi-source work needs actual bounds to retain its aggregate allowance.
        minimum_faces = minimum // face_bytes if len(sources) == 1 else 0
        counts = tuple(
            _source_triangle_count(source, minimum_faces=minimum_faces)
            for source in sources
        )
        counted = all(count is not None for count in counts)
        claim = request(pool, counts, work=work)
    record_estimate(
        EstimateStats(
            file_types=tuple(
                source.file_type.lower().lstrip(".") for source in sources
            ),
            source_bytes=_source_bytes(sources),
            basis=EstimateBasis.COUNTED if counted else EstimateBasis.UNKNOWN,
            requested_bytes=claim.bytes,
            elapsed_ns=time.monotonic_ns() - started,
        )
    )
    return claim

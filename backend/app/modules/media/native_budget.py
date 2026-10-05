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


def estimate_sources(
    pool: Resources,
    sources: tuple[MeshSource, ...],
    *,
    work: WorkProfile = GeometryWork(),
) -> Resources:
    from app.modules.media.stl_reader import binary_stl_info

    counts: list[int | None] = []
    for source in sources:
        info = (
            binary_stl_info(source.path)
            if source.file_type.lower().lstrip(".") == "stl"
            else None
        )
        counts.append(info[0] if info is not None else None)
    return request(pool, tuple(counts), work=work)

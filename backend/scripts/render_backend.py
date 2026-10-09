"""Backend-neutral contracts for optional raster qualification tools.

Production rendering stays CPU-only until independent hardware qualification.
Native handles never cross these operation-specific ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Protocol

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float32] | NDArray[np.float64]
Shade = Callable[[FloatArray], FloatArray]


class Candidate(StrEnum):
    MODERNGL = "moderngl"
    WGPU = "wgpu"


class GpuFailure(StrEnum):
    INVALID_REQUEST = "invalid_request"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    CONTEXT_FAILED = "context_failed"
    SHADER_FAILED = "shader_failed"
    ALLOCATION_FAILED = "allocation_failed"
    DRAW_FAILED = "draw_failed"
    READBACK_FAILED = "readback_failed"
    DEVICE_LOST = "device_lost"
    CLOSED = "closed"


class GpuError(Exception):
    def __init__(self, reason: GpuFailure):
        self.reason = reason
        super().__init__(reason.value)


@dataclass
class GpuStats:
    requested_allocation_bytes: int
    retained_geometry_bytes: int = 0
    upload_bytes: int = 0
    draw_calls: int = 0
    readback_count: int = 0
    upload_ms: float = 0.0
    draw_ms: float = 0.0
    readback_ms: float = 0.0


class RenderFrame(Protocol):
    stats: GpuStats
    failure: GpuError | None

    def __call__(
        self,
        img: NDArray[np.uint8],
        zbuf: FloatArray,
        tri: FloatArray,
        vert_nrm: FloatArray,
        shade: Shade,
        base_color: FloatArray,
        width: int,
        height: int,
    ) -> None: ...

    def finish(self, img: NDArray[np.uint8], zbuf: FloatArray) -> None: ...

    def close(self) -> None: ...


class RenderSession(Protocol):
    info: dict[str, str | int | bool]

    def close(self) -> None: ...

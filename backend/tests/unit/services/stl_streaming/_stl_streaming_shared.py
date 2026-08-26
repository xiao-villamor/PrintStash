"""Focused coverage for the isolated large-STL preview path."""

from __future__ import annotations

import io
import json
import math
import signal
import struct
import time
from pathlib import Path
from typing import Any

import pytest

from app.core.config import _overlay
from app.services import mesh_processing
from app.services.stl_streaming import (
    STLStreamingLimits,
    STLStreamingResult,
    render_stl_preview_isolated,
)

_RECORD = struct.Struct("<12fH")


def _binary_triangle_stl(
    path: Path, count: int = 12, offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> None:
    triangles = []
    for index in range(count):
        x = float(index % 4) + offset[0]
        y = float(index // 4) + offset[1]
        z = offset[2]
        triangles.append(
            _RECORD.pack(
                0.0,
                0.0,
                1.0,
                x,
                y,
                z,
                x + 0.8,
                y,
                z,
                x,
                y + 0.8,
                z,
                0,
            )
        )
    path.write_bytes(
        b"streaming-test".ljust(80, b"\0")
        + struct.pack("<I", count)
        + b"".join(triangles)
    )


def _ascii_triangle_stl(path: Path) -> None:
    path.write_text(
        """solid streaming
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid streaming
"""
    )


def _binary_annulus_stl(path: Path, segments: int = 48) -> None:
    """Write a thin ring so the streaming worker's center stays transparent."""
    outer, inner = 10.0, 4.0
    record = struct.Struct("<12fH")
    triangles: list[bytes] = []

    def point(radius: float, index: int) -> tuple[float, float, float]:
        angle = 2.0 * math.pi * index / segments
        return radius * math.cos(angle), radius * math.sin(angle), 0.0

    for index in range(segments):
        next_index = (index + 1) % segments
        outer0, outer1 = point(outer, index), point(outer, next_index)
        inner0, inner1 = point(inner, index), point(inner, next_index)
        triangles.extend(
            [
                record.pack(0.0, 0.0, 1.0, *outer0, *outer1, *inner1, 0),
                record.pack(0.0, 0.0, 1.0, *outer0, *inner1, *inner0, 0),
            ]
        )
    path.write_bytes(
        b"streaming-annulus".ljust(80, b"\0")
        + struct.pack("<I", len(triangles))
        + b"".join(triangles)
    )


def _limits() -> STLStreamingLimits:
    return STLStreamingLimits(
        max_triangles=1_000,
        max_source_bytes=1_000_000,
        max_candidates=1_000_000,
        soft_timeout_seconds=5,
        hard_timeout_seconds=10,
        max_rss_bytes=256 * 1024 * 1024,
        address_space_bytes=512 * 1024 * 1024,
    )


def _valid_png(width: int = 32, height: int = 24) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (width, height), (90, 140, 210, 255)).save(output, format="PNG")
    return output.getvalue()


def _manifest(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "version": 1,
        "status": "complete",
        "width": 32,
        "height": 24,
        "triangle_count": 2,
        "parsed_triangles": 2,
        "scanned_bytes": 184,
        "raster_candidates": 16,
        "bounds_min": [0.0, 0.0, 0.0],
        "bounds_max": [1.0, 1.0, 1.0],
    }
    result.update(overrides)
    return result


def _worker_limits(deadline: float | None = None):
    from app.services import stl_preview_worker

    return stl_preview_worker._Limits(
        max_triangles=1_000,
        max_source_bytes=1_000_000,
        max_candidates=1_000_000,
        chunk_triangles=2,
        max_lines=1_000,
        max_line_bytes=1_000,
        deadline=time.monotonic() + 5 if deadline is None else deadline,
    )


def _worker_cli_args(
    source: Path,
    output: Path,
    manifest: Path,
    **overrides: object,
) -> list[str]:
    values: dict[str, object] = {
        "width": 48,
        "height": 36,
        "max_triangles": 1_000,
        "max_source_bytes": 1_000_000,
        "max_candidates": 1_000_000,
        "chunk_triangles": 128,
        "max_lines": 10_000,
        "max_line_bytes": 64 * 1024,
        "timeout_seconds": 5,
        "address_space_bytes": 512 * 1024 * 1024,
        "cpu_seconds": 7,
        "expected_parent_pid": 1,
    }
    values.update(overrides)
    return [
        str(source),
        str(output),
        str(manifest),
        str(values["width"]),
        str(values["height"]),
        "--max-triangles",
        str(values["max_triangles"]),
        "--max-source-bytes",
        str(values["max_source_bytes"]),
        "--max-candidates",
        str(values["max_candidates"]),
        "--chunk-triangles",
        str(values["chunk_triangles"]),
        "--max-lines",
        str(values["max_lines"]),
        "--max-line-bytes",
        str(values["max_line_bytes"]),
        "--timeout-seconds",
        str(values["timeout_seconds"]),
        "--address-space-bytes",
        str(values["address_space_bytes"]),
        "--cpu-seconds",
        str(values["cpu_seconds"]),
        "--expected-parent-pid",
        str(values["expected_parent_pid"]),
    ]


__all__ = [
    "Any",
    "Path",
    "STLStreamingLimits",
    "STLStreamingResult",
    "_RECORD",
    "_ascii_triangle_stl",
    "_binary_annulus_stl",
    "_binary_triangle_stl",
    "_limits",
    "_manifest",
    "_overlay",
    "_valid_png",
    "_worker_cli_args",
    "_worker_limits",
    "io",
    "json",
    "mesh_processing",
    "pytest",
    "render_stl_preview_isolated",
    "signal",
    "struct",
    "time",
]

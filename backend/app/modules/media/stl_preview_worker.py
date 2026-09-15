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

_WORKER_VERSION = 1
_MAX_RENDER_DIMENSION = 2048
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


def _apply_worker_limits(
    address_space: int, cpu_seconds: int, *, expected_parent_pid: int
) -> None:
    """Apply process limits before loading Rust and protect parent death."""

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
        from printstash_core.mesh.native_rasterizer import kernel
        from printstash_core.mesh.preview_profile import PREVIEW_PROFILE as p

        result = kernel().render_stl_streaming(
            args.source,
            args.width,
            args.height,
            limits.max_triangles,
            limits.max_source_bytes,
            limits.max_candidates,
            limits.chunk_triangles,
            max(0.001, limits.deadline - time.monotonic()),
            (
                p.margin_fraction,
                p.hero_azimuth_degrees,
                p.hero_elevation_degrees,
                p.flat_tilt_degrees,
                p.flat_thickness_ratio,
                *p.material_albedo,
            ),
            limits.max_lines,
            limits.max_line_bytes,
        )
        image, count, _parsed, lower, upper, scanned, _complete, candidates, seconds = (
            result
        )
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_bytes(image)
        os.replace(temporary, args.output)
        _write_manifest(
            args.manifest,
            {
                "version": _WORKER_VERSION,
                "status": "complete",
                "width": args.width,
                "height": args.height,
                "triangle_count": count,
                "parsed_triangles": count,
                "scanned_bytes": scanned,
                "raster_candidates": candidates,
                "bounds_min": list(lower),
                "bounds_max": list(upper),
                "rust_stage_seconds": list(seconds),
            },
        )
        return 0
    except (_InvalidSTL, OSError, ValueError, struct.error):
        return 3
    except Exception:
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

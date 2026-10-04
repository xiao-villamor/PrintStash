"""Bounded, isolated STL preview rendering.

The API process never parses a large STL with NumPy or Trimesh.  It starts a
short-lived worker which performs two sequential passes over the file and
returns a small, validated result.  The worker is deliberately a private seam:
callers only need the result object and do not depend on its wire format.
"""

from __future__ import annotations

import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.media import native_process
from app.modules.media.stl_reader import STLMeasurements
from app.modules.media.worker_bootstrap import WORKER_MARKER

logger = get_logger(__name__)

_WORKER_VERSION = 1
_MAX_RENDER_DIMENSION = 2048
_MAX_MANIFEST_BYTES = 16 * 1024
_MAX_PNG_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_TRIANGLES = 20_000_000
_DEFAULT_MAX_SOURCE_BYTES = 1 << 30
_DEFAULT_MAX_CANDIDATES = 20_000_000
_DEFAULT_WORKER_RSS_BYTES = 256 * 1024 * 1024
_DEFAULT_WORKER_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
_MAX_WORKER_TRIANGLES = 20_000_000
_MAX_WORKER_SOURCE_BYTES = 1 << 30
_MAX_WORKER_CANDIDATES = 20_000_000


@dataclass(frozen=True)
class STLStreamingLimits:
    """Budgets passed to the worker and repeated in its manifest."""

    max_triangles: int = _DEFAULT_MAX_TRIANGLES
    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES
    max_candidates: int = _DEFAULT_MAX_CANDIDATES
    chunk_triangles: int = 8192
    max_lines: int = 10_000_000
    max_line_bytes: int = 64 * 1024
    soft_timeout_seconds: float = 45.0
    hard_timeout_seconds: float = 60.0
    max_rss_bytes: int = _DEFAULT_WORKER_RSS_BYTES
    address_space_bytes: int = _DEFAULT_WORKER_ADDRESS_SPACE_BYTES

    def as_worker_args(self, *, expected_parent_pid: int) -> list[str]:
        cpu_seconds = max(int(self.hard_timeout_seconds) + 2, 5)
        return [
            "--max-triangles",
            str(self.max_triangles),
            "--max-source-bytes",
            str(self.max_source_bytes),
            "--max-candidates",
            str(self.max_candidates),
            "--chunk-triangles",
            str(self.chunk_triangles),
            "--max-lines",
            str(self.max_lines),
            "--max-line-bytes",
            str(self.max_line_bytes),
            "--timeout-seconds",
            str(self.soft_timeout_seconds),
            "--address-space-bytes",
            str(self.address_space_bytes),
            "--cpu-seconds",
            str(cpu_seconds),
            "--expected-parent-pid",
            str(expected_parent_pid),
        ]


@dataclass(frozen=True)
class STLStreamingResult:
    """A complete preview and exact metadata from the streamed source."""

    png: bytes
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    triangle_count: int
    parsed_triangles: int
    scanned_bytes: int
    raster_candidates: int


def _effective_limits() -> STLStreamingLimits:
    """Return safe defaults, allowing only a bounded timeout override."""

    timeout = 45.0
    try:
        timeout = float(getattr(settings, "mesh_stream_timeout_seconds", 45))
    except TypeError, ValueError:
        pass
    timeout = min(max(timeout, 1.0), 45.0)
    return STLStreamingLimits(
        soft_timeout_seconds=timeout,
        hard_timeout_seconds=min(max(timeout + 15.0, 15.0), 60.0),
    )


def _worker_memory_budget() -> int:
    """Use the existing per-job RAM share when it is lower than our hard target."""

    try:
        from app.modules.media.native_process import native_memory_budget_bytes

        budget = native_memory_budget_bytes()
    except Exception:  # pragma: no cover - defensive import boundary
        budget = None
    if budget is None:
        return _DEFAULT_WORKER_RSS_BYTES
    return max(min(int(budget), _DEFAULT_WORKER_RSS_BYTES), 32 * 1024 * 1024)


def _within_worker_hard_bounds(limits: STLStreamingLimits) -> bool:
    """Reject caller-supplied budgets that could weaken worker isolation."""

    return (
        0 < limits.max_triangles <= _MAX_WORKER_TRIANGLES
        and 0 < limits.max_source_bytes <= _MAX_WORKER_SOURCE_BYTES
        and 0 < limits.max_candidates <= _MAX_WORKER_CANDIDATES
        and 0 < limits.chunk_triangles <= 8192
        and 0 < limits.max_lines <= 10_000_000
        and 0 < limits.max_line_bytes <= 64 * 1024
        and 0 < limits.soft_timeout_seconds <= 45
        and 0 < limits.hard_timeout_seconds <= 60
        and 0 < limits.max_rss_bytes
        and 0 < limits.address_space_bytes <= _DEFAULT_WORKER_ADDRESS_SPACE_BYTES
    )


def _valid_manifest(manifest: object, *, width: int, height: int) -> bool:
    if not isinstance(manifest, dict):
        return False
    if (
        manifest.get("version") != _WORKER_VERSION
        or manifest.get("status") != "complete"
    ):
        return False
    if manifest.get("width") != width or manifest.get("height") != height:
        return False
    for key in (
        "triangle_count",
        "parsed_triangles",
        "scanned_bytes",
        "raster_candidates",
    ):
        value = manifest.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return False
    if manifest["parsed_triangles"] != manifest["triangle_count"]:
        return False
    for key in ("bounds_min", "bounds_max"):
        value = manifest.get(key)
        if (
            not isinstance(value, list)
            or len(value) != 3
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(float(item))
                for item in value
            )
        ):
            return False
    lower = manifest["bounds_min"]
    upper = manifest["bounds_max"]
    return all(float(lo) <= float(hi) for lo, hi in zip(lower, upper, strict=True))


def _decode_result(
    output: Path,
    manifest_path: Path,
    *,
    width: int,
    height: int,
    limits: STLStreamingLimits,
) -> STLStreamingResult | None:
    try:
        with manifest_path.open("rb") as manifest_file:
            manifest_bytes = manifest_file.read(_MAX_MANIFEST_BYTES + 1)
        if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
            return None
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not _valid_manifest(manifest, width=width, height=height):
            return None
        if manifest["triangle_count"] > limits.max_triangles:
            return None
        if manifest["scanned_bytes"] > limits.max_source_bytes:
            return None
        if manifest["raster_candidates"] > limits.max_candidates:
            return None
        with output.open("rb") as output_file:
            data = output_file.read(_MAX_PNG_BYTES + 1)
        if len(data) > _MAX_PNG_BYTES:
            return None
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (width, height):
                return None
            image.verify()
        # `_valid_manifest` ran first and returned early unless both bounds are
        # three-element lists of finite, non-bool numbers, so re-checking the type
        # here could only ever add a branch nothing reaches.
        lower_values = manifest["bounds_min"]
        upper_values = manifest["bounds_max"]
        lower = (
            float(lower_values[0]),
            float(lower_values[1]),
            float(lower_values[2]),
        )
        upper = (
            float(upper_values[0]),
            float(upper_values[1]),
            float(upper_values[2]),
        )
        return STLStreamingResult(
            png=data,
            bounds_min=lower,
            bounds_max=upper,
            triangle_count=int(manifest["triangle_count"]),
            parsed_triangles=int(manifest["parsed_triangles"]),
            scanned_bytes=int(manifest["scanned_bytes"]),
            raster_candidates=int(manifest["raster_candidates"]),
        )
    except OSError, ValueError, TypeError, KeyError, json.JSONDecodeError:
        return None
    except Exception:  # noqa: BLE001 - Pillow must not take down ingestion
        return None


def render_stl_preview_isolated(
    path: Path,
    *,
    width: int = 640,
    height: int = 480,
    limits: STLStreamingLimits | None = None,
    measurements: STLMeasurements | None = None,
) -> STLStreamingResult | None:
    """Render *path* in a disposable worker with bounded memory and CPU.

    An outer native worker may reuse its exact scan for the render pass. That
    hint remains in-process; standalone callers retain the original two passes.
    """
    in_worker = os.environ.get(WORKER_MARKER) == str(os.getpid())
    if measurements is not None and not in_worker:
        raise ValueError("measurements require an owned worker")

    if not (1 <= int(width) <= _MAX_RENDER_DIMENSION) or not (
        1 <= int(height) <= _MAX_RENDER_DIMENSION
    ):
        return None
    limits = limits or _effective_limits()
    if path.suffix.lower() != ".stl":
        return None
    if not _within_worker_hard_bounds(limits):
        return None
    try:
        if path.stat().st_size > limits.max_source_bytes:
            logger.warning("stl_streaming: source exceeds byte budget: %s", path.name)
            return None
    except OSError:
        return None

    worker_limits = STLStreamingLimits(
        max_triangles=limits.max_triangles,
        max_source_bytes=limits.max_source_bytes,
        max_candidates=limits.max_candidates,
        chunk_triangles=limits.chunk_triangles,
        max_lines=limits.max_lines,
        max_line_bytes=limits.max_line_bytes,
        soft_timeout_seconds=limits.soft_timeout_seconds,
        hard_timeout_seconds=limits.hard_timeout_seconds,
        max_rss_bytes=min(limits.max_rss_bytes, _worker_memory_budget()),
        address_space_bytes=limits.address_space_bytes,
    )
    from app.modules.media.mesh_isolation import MeshWorkerError, supervise_result
    from app.modules.media.native_budget import MeshSource, estimate_sources
    from app.modules.media.native_execution import admission
    from app.modules.media.worker_bootstrap import WorkerLifecycle
    from app.modules.media.worker_bootstrap import command as worker_command
    from app.runtime.native_runtime import current_permit

    capacity = native_process.native_capacity()
    active = current_permit()
    amount = (
        active.resources
        if active is not None
        else estimate_sources(capacity, (MeshSource(path, "stl"),))
    )
    with (
        admission(amount, capacity, checkpoint=checkpoint) as permit,
        tempfile.TemporaryDirectory(prefix="printstash-mesh-") as temporary,
    ):
        root = Path(temporary)
        output = root / "preview.png.part"
        manifest = root / "result.json"
        arguments = [
            str(path.absolute()),
            str(output),
            str(manifest),
            str(int(width)),
            str(int(height)),
            *worker_limits.as_worker_args(expected_parent_pid=os.getpid()),
        ]
        if in_worker:
            # The outer worker already owns a hard ceiling and deadline.
            # Streaming consumes that allowance, rather than spawning a second
            # process with another full allowance.
            from app.modules.media.stl_preview_worker import main

            if main(arguments, apply_limits=False, measurements=measurements) != 0:
                return None
            return _decode_result(
                output,
                manifest,
                width=int(width),
                height=int(height),
                limits=worker_limits,
            )
        try:
            supervised = supervise_result(
                worker_command(
                    "app.modules.media.stl_preview_worker",
                    arguments,
                    permit.resources.bytes,
                ),
                memory_budget=min(worker_limits.max_rss_bytes, permit.resources.bytes),
                timeout_seconds=worker_limits.hard_timeout_seconds,
                permit=permit,
                lifecycle=WorkerLifecycle.GUARDED,
                temporary_directory=root,
            )
        except MeshWorkerError as exc:
            logger.warning(
                "stl_streaming: worker failed for %s (%s)", path.name, exc.reason.value
            )
            return None
        result = _decode_result(
            output,
            manifest,
            width=int(width),
            height=int(height),
            limits=worker_limits,
        )
        if result is None:
            logger.warning(
                "stl_streaming: worker returned invalid result for %s", path.name
            )
        else:
            logger.info(
                "stl_streaming: rendered %s (%d triangles, %d candidates, %.2fs, peak_rss=%s)",
                path.name,
                result.triangle_count,
                result.raster_candidates,
                supervised.stats.elapsed_ns / 1_000_000_000,
                supervised.stats.peak_tree_rss_bytes,
            )
        return result


__all__ = [
    "STLStreamingLimits",
    "STLStreamingResult",
    "render_stl_preview_isolated",
]

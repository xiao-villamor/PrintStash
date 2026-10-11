"""Continue a thumbnail after its parser is reaped, retaining bounded custody."""

from __future__ import annotations

import json
import time
from typing import Literal

from app.core.cancellation import checkpoint
from app.modules.media import native_process
from app.modules.media.compute_geometry_wire import split_thumbnail
from app.modules.media.compute_render import render_staged
from app.modules.media.mesh_contracts import (
    MeshCoverage,
    PreviewCoverage,
    ThumbnailFailureReason,
    ThumbnailStrategy,
)
from app.modules.media.mesh_protocol import PreparedThumbnailOutput, ThumbnailOutput
from app.modules.media.native_budget import RasterCodec, RasterWork
from app.modules.media.native_budget import request as native_request
from app.modules.media.native_execution import admission
from app.modules.media.thumbnail_staging import FinalizationMode, ThumbnailStaging
from app.modules.media.worker_bootstrap import WorkerLifecycle
from app.modules.media.worker_bootstrap import command as worker_command
from app.runtime.native_admission import Resources
from app.runtime.native_runtime import current_permit


def finish(
    staging: ThumbnailStaging,
    prepared: PreparedThumbnailOutput,
    *,
    output_format: Literal["PNG", "WEBP"],
    deadline: float,
    parser_execution_id: str,
) -> ThumbnailOutput:
    """GPU wait owns prepared bytes only; the finalizer reacquires native RAM."""
    from app.modules.media.mesh_isolation import (
        MeshWorkerCancelled,
        MeshWorkerError,
        memory_budget_bytes,
        runtime_overrides,
        supervise_result,
    )

    if current_permit() is not None:
        raise RuntimeError("staged rendering requires the parser permit to be released")
    started = time.monotonic()

    def remaining() -> None:
        checkpoint()
        if time.monotonic() >= deadline:
            raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)

    remaining()
    read_started = time.monotonic_ns()
    payload = staging.read(prepared.render)
    wire = split_thumbnail(payload)
    if (wire.width, wire.height) != (staging.width, staging.height):
        raise ValueError("staged_thumbnail_dimensions")
    face_count = wire.face_count
    prepared_read_ns = time.monotonic_ns() - read_started
    dispatch_started = time.monotonic_ns()
    pixels = render_staged(wire, deadline=deadline)
    dispatch_ns = time.monotonic_ns() - dispatch_started
    remaining()
    mode = FinalizationMode.RENDER
    if pixels is not None:
        staging.write_pixels(pixels.rgba)
        mode = FinalizationMode.ENCODE
    # No packet, view, or RPC response survives into native encoder admission.
    del wire, payload, pixels
    pool = Resources(native_process.native_capacity().slots, memory_budget_bytes())
    work = RasterWork(
        staging.width, staging.height, 1, RasterCodec(output_format.lower())
    )
    amount = native_request(
        pool, (0 if mode is FinalizationMode.ENCODE else face_count,), work=work
    )
    queued = time.monotonic_ns()
    with admission(amount, pool, checkpoint=remaining) as permit:
        queue_ns = time.monotonic_ns() - queued
        remaining()
        spec = {
            "overrides": runtime_overrides(),
            "mode": mode.value,
            "directory": str(staging.directory),
            "width": staging.width,
            "height": staging.height,
            "output_format": output_format,
            "size": prepared.render.size,
            "sha256": prepared.render.sha256,
        }
        try:
            reply = supervise_result(
                worker_command(
                    "app.modules.media.thumbnail_finalize_worker",
                    [json.dumps(spec)],
                    permit.resources.bytes,
                ),
                memory_budget=permit.resources.bytes,
                timeout_seconds=deadline - time.monotonic(),
                permit=permit,
                lifecycle=WorkerLifecycle.GUARDED,
            )
        except (MeshWorkerError, MeshWorkerCancelled) as exc:
            if exc.supervision is not None:
                from app.modules.media.mesh_observability import (
                    record_thumbnail_handoff,
                )

                record_thumbnail_handoff(
                    parser_execution_id=parser_execution_id,
                    finalizer=exc.supervision,
                    mode=mode,
                    prepared_bytes=prepared.render.size,
                    prepared_read_ns=prepared_read_ns,
                    dispatch_ns=dispatch_ns,
                    finalizer_queue_ns=queue_ns,
                )
            raise
    remaining()
    if not reply.payload:
        raise MeshWorkerError(ThumbnailFailureReason.RENDERER_NO_OUTPUT)
    from app.modules.media.mesh_observability import record_thumbnail_handoff

    record_thumbnail_handoff(
        parser_execution_id=parser_execution_id,
        finalizer=reply.stats,
        mode=mode,
        prepared_bytes=prepared.render.size,
        prepared_read_ns=prepared_read_ns,
        dispatch_ns=dispatch_ns,
        finalizer_queue_ns=queue_ns,
    )
    peak = (
        max(prepared.peak_rss_bytes or 0, reply.stats.peak_tree_rss_bytes or 0) or None
    )
    return ThumbnailOutput(
        reply.payload,
        ThumbnailStrategy.FULL,
        MeshCoverage(
            prepared.coverage.source_scan,
            prepared.coverage.geometry,
            PreviewCoverage.COMPLETE,
        ),
        None,
        prepared.duration_ms + max(0, round((time.monotonic() - started) * 1000)),
        peak,
    )

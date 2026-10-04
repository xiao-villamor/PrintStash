"""Raw visual outputs accepted only after complete supervised worker execution."""

from __future__ import annotations

import re
import struct
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput
from printstash_core.inference.context import InferenceContext
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.visual_inputs import VisualRecipe

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.modules.media import mesh_isolation, native_process
from app.modules.media.geometry_analysis import VisualViews
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_telemetry import WorkerExitCause
from app.modules.media.native_budget import MeshSource, estimate_sources
from app.modules.media.native_execution import admission
from app.modules.media.visual_worker import MAX_REPLY
from app.modules.media.worker_bootstrap import WorkerLifecycle
from app.modules.media.worker_bootstrap import command as worker_command
from app.runtime.native_runtime import current_permit


def render(
    path: Path,
    *,
    file_type: str,
    recipe: VisualRecipe | PointRecipe,
    context: InferenceContext,
) -> VisualViews:
    """Share admission and process containment with all other native mesh work."""

    def remaining() -> None:
        checkpoint()
        context.remaining()

    remaining()
    capacity = native_process.native_capacity()
    existing = current_permit()
    amount = (
        existing.resources
        if existing is not None
        else estimate_sources(capacity, (MeshSource(path, file_type),))
    )
    with admission(amount, capacity, checkpoint=remaining) as permit:
        try:
            reply = mesh_isolation.supervise_result(
                worker_command(
                    "app.modules.media.visual_worker",
                    [str(path), file_type, recipe.encode()],
                    permit.resources.bytes,
                ),
                memory_budget=permit.resources.bytes,
                timeout_seconds=context.remaining(),
                permit=permit,
                lifecycle=WorkerLifecycle.GUARDED,
                environment={
                    "VAULT_MESH_MAX_RENDER_TRIANGLES": str(
                        min(MAX_ANALYSIS_FACES, settings.mesh_max_render_triangles)
                    ),
                },
                reply_limit=MAX_REPLY + 4,
                withdrawn=context.cancelled,
            )
        except mesh_isolation.MeshWorkerCancelled:
            checkpoint(force=True)
            if context.cancelled():
                raise EmbeddingError("inference_cancelled") from None
            raise
        except mesh_isolation.MeshWorkerError as exc:
            if exc.reason is ThumbnailFailureReason.TIMEOUT:
                code = "inference_timeout"
            elif exc.reason is ThumbnailFailureReason.RESOURCE_LIMIT:
                code = "embedding_worker_oom"
            elif (
                exc.supervision is not None
                and exc.supervision.exit_cause is WorkerExitCause.REPLY_LIMIT
            ):
                code = "embedding_output_budget"
            else:
                code = "embedding_render_failed"
            raise EmbeddingError(code) from exc
        payload = reply.payload
        if len(payload) < 8:
            raise EmbeddingError("embedding_output_invalid")
        expected = struct.unpack("!I", payload[:4])[0]
        if not 4 <= expected <= MAX_REPLY or len(payload) != expected + 4:
            raise EmbeddingError("embedding_output_invalid")
        remaining()
        return decode_reply(payload[4:], recipe)


def decode_reply(payload: bytes, recipe: VisualRecipe | PointRecipe) -> VisualViews:
    if payload.startswith(b"ERR1"):
        code = payload[4:].decode("ascii", errors="replace")
        raise EmbeddingError(
            code
            if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code)
            else "embedding_render_failed"
        )
    if isinstance(recipe, PointRecipe):
        if payload[:4] != b"PNT1" or len(payload) != 240004:
            raise EmbeddingError("embedding_output_invalid")
        points = EmbeddingInput("point_cloud", points=payload[4:])
        return VisualViews(None, (points,))
    if len(payload) < 9 or payload[:4] != b"RGB1":
        raise EmbeddingError("embedding_output_invalid")
    width, height, count = struct.unpack("!HHB", payload[4:9])
    size = width * height * 3
    if (
        width != recipe.image_size
        or height != width
        or count != recipe.view_count + 1
        or len(payload) != 9 + size * count
    ):
        raise EmbeddingError("embedding_output_invalid")
    views = tuple(
        EmbeddingInput(
            "image",
            rgb=payload[9 + i * size : 9 + (i + 1) * size],
            width=width,
            height=height,
        )
        for i in range(count)
    )
    return VisualViews(views[0], views[1:])

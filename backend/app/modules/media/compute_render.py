"""Optional render dispatch; canonical CPU rendering remains the fallback."""

import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING
from uuid import uuid4

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE, RASTERIZER_RECIPE
from printstash_core.mesh.rasterizer import RenderedPixels, postprocess_rgba

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.work_priority import WorkPriority, current_priority
from app.runtime.compute import client
from app.runtime.compute.contracts import ComputeUnavailable
from app.runtime.compute.protocol import BinaryRenderRequest, Priority

if TYPE_CHECKING:
    from .compute_geometry_wire import ThumbnailWire


RECIPE = "webgpu-native-raster-v4"
_background_render: ContextVar[bool] = ContextVar("background_render", default=False)


@contextmanager
def background_render_scope() -> Iterator[None]:
    """Derivative Jobs may coalesce even when their discovery tier is interactive.

    This changes only compute dispatch. FIFO Job ordering and native admission
    retain their original scheduling priority; direct query renders stay urgent.
    """
    token = _background_render.set(True)
    try:
        yield
    finally:
        _background_render.reset(token)


def render_priority() -> Priority:
    return (
        Priority.BACKGROUND
        if _background_render.get() or current_priority() is WorkPriority.BACKFILL
        else Priority.INTERACTIVE
    )


def render(prepared, width, height, views, matte):
    if not client.available(render=True):
        return None
    from .compute_geometry import encode, split

    checkpoint()
    try:
        payload = encode(prepared, width, height, views, matte)
        geometry_key, header, body = split(payload)
        request = BinaryRenderRequest(
            deadline=time.monotonic() + min(settings.mesh_step_timeout_seconds, 300),
            priority=render_priority(),
            request_id=uuid4().hex,
            geometry_key=geometry_key,
            geometry_bytes=len(body),
            header=header,
            recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:{width}x{height}:{len(views)}:{int(matte)}",
            units=prepared.face_count * len(views),
        )
        response = client.exchange_render(request, body, checkpoint=checkpoint)
        if not response or response[0] not in (0, 1):
            raise ValueError("compute_render_output")
        finalized, data = bool(response[0]), response[1:]
        factor = PREVIEW_PROFILE.supersample_for(width)
        w, h = width * factor, height * factor
        if len(data) < 12:
            raise ValueError("compute_render_output")
        actual_width, actual_height, count = struct.unpack("!III", data[:12])
        if count != len(views) or (actual_width, actual_height) not in (
            (width, height),
            (w, h),
        ):
            raise ValueError("compute_render_output")
        if (actual_width, actual_height) != ((width, height) if finalized else (w, h)):
            raise ValueError("compute_render_output")
        size = actual_width * actual_height * 4
        if len(data) != 12 + size * count:
            raise ValueError("compute_render_output")
        checkpoint()
        frames = []
        for i in range(len(views)):
            checkpoint()
            pixels = data[12 + i * size : 12 + (i + 1) * size]
            frames.append(
                RenderedPixels(width, height, pixels)
                if finalized
                else postprocess_rgba(pixels, width, height)
            )
        checkpoint()
        return tuple(frames)
    except ComputeUnavailable, ValueError, KeyError:
        checkpoint()
        return None


def render_staged(wire: "ThumbnailWire", *, deadline: float) -> RenderedPixels | None:
    """Dispatch validated owned bytes without importing a numerical runtime.

    Postprocessing is part of the accelerated response. A broker that returns
    intermediate pixels leaves the whole bounded fallback to the isolated worker.
    """
    checkpoint()
    if not client.available(render=True):
        return None
    request = BinaryRenderRequest(
        deadline=min(
            deadline, time.monotonic() + min(settings.mesh_step_timeout_seconds, 300)
        ),
        priority=Priority.BACKGROUND,
        request_id=uuid4().hex,
        geometry_key=wire.geometry_key,
        geometry_bytes=len(wire.body),
        header=wire.header,
        recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:{wire.width}x{wire.height}:1:0",
        units=wire.face_count,
    )
    try:
        response = client.exchange_render(
            request,
            wire.body,
            checkpoint=checkpoint,
            response_limit=14 + wire.width * wire.height * 4,
        )
        size = wire.width * wire.height * 4
        if (
            len(response) != 13 + size
            or response[0] != 1
            or struct.unpack_from("!III", response, 1) != (wire.width, wire.height, 1)
        ):
            raise ValueError("compute_staged_output")
        checkpoint()
        return RenderedPixels(wire.width, wire.height, response[13:])
    except ComputeUnavailable, ValueError:
        checkpoint()
        return None

"""Optional render dispatch; canonical CPU rendering remains the fallback."""

import struct
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE, RASTERIZER_RECIPE
from printstash_core.mesh.rasterizer import RenderedPixels, postprocess_rgba

from app.core.cancellation import OperationCancelled, checkpoint
from app.core.config import settings
from app.core.work_priority import WorkPriority, current_priority
from app.runtime.compute import client
from app.runtime.compute.contracts import ComputeUnavailable, Reason
from app.runtime.compute.protocol import MAX_FRAME, BinaryRenderRequest, Priority

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


def render_recipe(width: int, height: int, views: int = 1, matte: bool = False) -> str:
    """One identity for eligibility checks and both render dispatch paths."""
    return f"{RECIPE}:{RASTERIZER_RECIPE}:{width}x{height}:{views}:{int(matte)}"


def _require_visible_frame(frame: RenderedPixels) -> RenderedPixels:
    # Inspect alpha without allocating a channel or importing an image runtime.
    # A blank accelerator reply is a rendering failure, not invalid source data.
    # Any nonzero alpha counts, including a single translucent pixel.
    if not any(memoryview(frame.rgba)[3::4]):
        raise ComputeUnavailable(Reason.DEVICE_FAILED)
    return frame


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
            recipe=render_recipe(width, height, len(views), matte),
            units=prepared.face_count * len(views),
        )
        factor = PREVIEW_PROFILE.supersample_for(width)
        w, h = width * factor, height * factor
        response = client.exchange_render(
            request,
            body,
            checkpoint=checkpoint,
            response_limit=min(MAX_FRAME, 14 + w * h * 4 * len(views)),
        )
        return _decode_frames(response, width, height, len(views))
    except ComputeUnavailable, ValueError, KeyError:
        checkpoint()
        return None


def _decode_frames(
    response: bytes,
    width: int,
    height: int,
    count: int,
    *,
    finalized_only: bool = False,
    check: Callable[[], None] = checkpoint,
) -> tuple[RenderedPixels, ...]:
    if len(response) < 13 or response[0] not in (0, 1):
        raise ValueError("compute_render_output")
    finalized = bool(response[0])
    if finalized_only and not finalized:
        # Staged callers own bounded final pixels only. The isolated fallback
        # handles a failed GPU postprocessor without moving raster work here.
        raise ComputeUnavailable(Reason.DEVICE_FAILED)
    factor = PREVIEW_PROFILE.supersample_for(width)
    expected = (width, height) if finalized else (width * factor, height * factor)
    actual_width, actual_height, actual_count = struct.unpack_from("!III", response, 1)
    if actual_count != count or (actual_width, actual_height) != expected:
        raise ValueError("compute_render_output")
    size = actual_width * actual_height * 4
    if len(response) != 13 + size * count:
        raise ValueError("compute_render_output")
    # Keep the immutable response owner alive, then copy each final frame once.
    # Slicing response[1:] would copy every pixel before that owned frame copy.
    data = memoryview(response)
    frames = []
    for i in range(count):
        check()
        pixels = bytes(data[13 + i * size : 13 + (i + 1) * size])
        frames.append(
            _require_visible_frame(
                RenderedPixels(width, height, pixels)
                if finalized
                else postprocess_rgba(pixels, width, height)
            )
        )
    check()
    return tuple(frames)


def render_packet(
    payload: bytes, *, width: int, height: int, deadline: float
) -> RenderedPixels | None:
    """Dispatch one reaped parser's immutable packet without decoding arrays.

    The caller retains its prepared-host reservation until dispatch and encoding
    finish. Invalid packets fail that handoff; only an unavailable/failed GPU
    returns None for guarded rendering from the same packet. Cancellation and
    the original absolute deadline always propagate.
    """
    from .compute_geometry_wire import (
        MAX_GEOMETRY_BYTES,
        body_size,
        descriptor,
        identity,
    )
    from .thumbnail_pixels import check_deadline

    def remaining() -> None:
        check_deadline(deadline)

    check_deadline(deadline, force=True)
    if type(payload) is not bytes:
        raise ValueError("compute_geometry_mutable")
    if not 4 <= len(payload) <= MAX_GEOMETRY_BYTES + 4100:
        raise ValueError("compute_geometry_budget")
    length = struct.unpack_from("!I", payload)[0]
    if not 0 < length <= 4096 or 4 + length > len(payload):
        raise ValueError("compute_geometry_header")
    header = payload[4 : 4 + length]
    value, layout = descriptor(header)
    if (
        type(width) is not int
        or type(height) is not int
        or (value["width"], value["height"]) != (width, height)
        or value["views"] != [None]
        or value["matte"] is not False
    ):
        raise ValueError("compute_thumbnail_frame")
    body = memoryview(payload)[4 + length :]
    if len(body) != body_size(layout):
        raise ValueError("compute_geometry_size")
    if not client.available(render=True):
        check_deadline(deadline, force=True)
        return None
    request = BinaryRenderRequest(
        deadline=min(deadline, time.monotonic() + 300),
        priority=Priority.BACKGROUND,
        request_id=uuid4().hex,
        geometry_key=identity(layout, body),
        geometry_bytes=len(body),
        header=header.decode(),
        recipe=render_recipe(width, height),
        units=layout.counts[1],
    )
    check_deadline(deadline, force=True)
    try:
        response = client.exchange_render(
            request,
            body,
            checkpoint=remaining,
            # A failed postprocessor may return bounded supersampled pixels.
            # Read their envelope so that tag0 selects guarded fallback, rather
            # than conflating a larger raw frame with invalid source geometry.
            response_limit=min(
                MAX_FRAME,
                14 + width * height * 4 * PREVIEW_PROFILE.supersample_for(width) ** 2,
            ),
        )
        result = _decode_frames(
            response, width, height, 1, finalized_only=True, check=remaining
        )[0]
    except ComputeUnavailable as exc:
        check_deadline(deadline, force=True)
        if exc.reason is Reason.DEADLINE:
            raise TimeoutError("thumbnail_deadline") from exc
        if exc.reason is Reason.CANCELLED:
            raise OperationCancelled() from exc
        if exc.reason is Reason.INVALID_INPUT:
            # Broker array/identity rejection is terminal data validation.
            # It must never trigger a second renderer on the same bad packet.
            raise ValueError("compute_geometry_invalid") from exc
        return None
    except ValueError, KeyError:
        check_deadline(deadline, force=True)
        return None
    check_deadline(deadline, force=True)
    return result

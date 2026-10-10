"""Optional render dispatch; canonical CPU rendering remains the fallback."""

import struct
import time
from uuid import uuid4

from printstash_core.mesh.preview_profile import PREVIEW_PROFILE, RASTERIZER_RECIPE
from printstash_core.mesh.rasterizer import RenderedPixels, postprocess_rgba

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.work_priority import WorkPriority, current_priority
from app.runtime.compute import client
from app.runtime.compute.contracts import ComputeUnavailable
from app.runtime.compute.protocol import BinaryRenderRequest, Priority

RECIPE = "webgpu-canonical-raster-v2"


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
            priority=Priority.INTERACTIVE
            if current_priority() == WorkPriority.INTERACTIVE
            else Priority.BACKGROUND,
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

"""Optional render dispatch; canonical CPU rendering remains the fallback."""

import base64
import json
import struct
import time
from uuid import uuid4

from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE
from printstash_core.mesh.rasterizer import RenderedPixels

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.work_priority import WorkPriority, current_priority
from app.runtime.compute import client
from app.runtime.compute.contracts import ComputeUnavailable, Reason
from app.runtime.compute.protocol import Priority, RenderRequest

RECIPE = "webgpu-canonical-raster-v2"


def render(prepared, width, height, views, matte):
    if not client.available(render=True):
        return None
    from .compute_geometry import encode

    checkpoint()
    try:
        payload = encode(prepared, width, height, views, matte)
        request = RenderRequest(
            deadline=time.monotonic() + min(settings.mesh_step_timeout_seconds, 300),
            priority=Priority.INTERACTIVE
            if current_priority() == WorkPriority.INTERACTIVE
            else Priority.BACKGROUND,
            request_id=uuid4().hex,
            payload=base64.b64encode(payload).decode(),
            recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:{width}x{height}:{len(views)}:{int(matte)}",
            units=prepared.face_count * len(views),
        )
        response = json.loads(client.exchange(request, checkpoint=checkpoint))
        if "error" in response:
            raise ComputeUnavailable(Reason(response["error"]))
        data = base64.b64decode(response["result"], validate=True)
        if len(data) < 12 or struct.unpack("!III", data[:12]) != (
            width,
            height,
            len(views),
        ):
            raise ValueError("compute_render_output")
        size = width * height * 4
        if len(data) != 12 + size * len(views):
            raise ValueError("compute_render_output")
        checkpoint()
        return tuple(
            RenderedPixels(width, height, data[12 + i * size : 12 + (i + 1) * size])
            for i in range(len(views))
        )
    except ComputeUnavailable, ValueError, KeyError:
        checkpoint()
        return None

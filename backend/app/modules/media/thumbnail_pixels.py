"""Encode bounded completed RGBA in its owning Job thread, without source parsing."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from app.core.cancellation import checkpoint

if TYPE_CHECKING:
    from printstash_core.mesh.rasterizer import RenderedPixels


def check_deadline(deadline: float, *, force: bool = False) -> None:
    # Transport and admission polls retain the Job probe's 200ms throttle.
    # The absolute monotonic deadline is always checked, even between probes.
    checkpoint(force=force)
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("thumbnail_deadline")


def encode_pixels(pixels: RenderedPixels, *, deadline: float) -> bytes:
    """Encode one canonical frame while the caller retains CPU/RAM custody.

    Pillow's bounded native call is synchronous: cancellation cannot release its
    reservation early. Check withdrawal and the original deadline again after
    the call, before returning bytes that the producer can publish.
    """
    check_deadline(deadline, force=True)
    width, height, rgba = pixels.width, pixels.height, pixels.rgba
    if (
        type(width) is not int
        or not 320 <= width <= 1280
        or type(height) is not int
        or height != round(width * 3 / 4)
        or type(rgba) is not bytes
        or len(rgba) != width * height * 4
    ):
        raise ValueError("thumbnail_frame_invalid")
    alpha = memoryview(rgba)[3::4]
    if not any(alpha):
        raise ValueError("thumbnail_empty")
    if (
        any(alpha[:width])
        or any(alpha[-width:])
        or any(alpha[::width])
        or any(alpha[width - 1 :: width])
    ):
        raise ValueError("thumbnail_frame_invalid")
    from printstash_core.mesh.rasterizer import encode_rendered_pixels

    check_deadline(deadline, force=True)
    encoded = encode_rendered_pixels(pixels, output_format="WEBP")
    check_deadline(deadline, force=True)
    return encoded

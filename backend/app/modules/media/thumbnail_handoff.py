"""Job-owned GPU thumbnail handoff with explicit host and codec custody."""

from __future__ import annotations

import json
import math
import secrets
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.core.cancellation import checkpoint
from app.core.logging import get_logger
from app.core.native_settings import settings
from app.runtime import preparation_runtime
from app.runtime.compute import client
from app.runtime.native_admission import Resources

from . import native_process
from .source_preparation import capacity as source_capacity
from .thumbnail_packet import MAX_PACKET_BYTES, PacketDescriptor, read_packet
from .thumbnail_pixels import check_deadline, encode_pixels

logger = get_logger(__name__)

HOST_CEILING = 384 * 1024**2
HOST_SLOTS = 4
CODEC_BASE_BYTES = 64 * 1024**2
CODEC_PIXEL_BYTES = 64


def resident_bytes(width: int, height: int) -> int:
    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

    pixels = width * height
    raw_pixels = pixels * PREVIEW_PROFILE.supersample_for(width) ** 2
    # A broker may return a bounded raw frame when its GPU finalizer is
    # unavailable. Receiving that frame still costs memory before CPU refusal.
    # Packet bytes die before codec entry; these two phase peaks do not coexist.
    return max(
        MAX_PACKET_BYTES + raw_pixels * 12,
        CODEC_BASE_BYTES + CODEC_PIXEL_BYTES * pixels + pixels * 12,
    )


def host_capacity(
    physical: int | None, geometry: int, model_fraction: float
) -> Resources | None:
    if (
        physical is None
        or physical <= 0
        or geometry <= 0
        or not math.isfinite(model_fraction)
        or not 0 < model_fraction < 1
    ):
        return None
    headroom = physical - geometry - int(physical * model_fraction)
    # At least 75% of the unassigned physical headroom remains for the OS and
    # ordinary application processes. Parser/model partitions are unchanged.
    amount = min(HOST_CEILING, headroom // 4)
    return Resources(HOST_SLOTS, amount) if amount > 0 else None


@dataclass(frozen=True)
class HandoffPlan:
    width: int
    height: int
    request: Resources
    capacity: Resources


def plan(width: int, height: int, source_bytes: int) -> HandoffPlan | None:
    from .compute_render import render_recipe

    if not 320 <= width <= 1280 or height != round(width * 3 / 4):
        return None
    capacity = host_capacity(
        native_process.memory_limit_bytes(),
        native_process.native_capacity().bytes,
        settings.embedding_memory_budget_fraction,
    )
    request = Resources(1, resident_bytes(width, height))
    if (
        capacity is None
        or not request.fits(capacity)
        or 2 * source_bytes + MAX_PACKET_BYTES > source_capacity().bytes
        or not client.render_ready(
            recipe=render_recipe(width, height), checkpoint=checkpoint
        )
    ):
        return None
    return HandoffPlan(width, height, request, capacity)


@dataclass(frozen=True)
class ThumbnailHandoff:
    plan: HandoffPlan
    workspace: Path
    deadline: float
    identity: str = field(default_factory=lambda: secrets.token_hex(12))

    def check(self, *, force: bool = False) -> None:
        check_deadline(self.deadline, force=force)

    def complete(
        self,
        packet: PacketDescriptor,
        directory: Path,
        directory_identity: tuple[int, int],
        *,
        fallback: Callable[[], bytes],
    ) -> bytes:
        from .compute_render import background_render_scope, render_packet

        payload = pixels = result = None
        try:
            started = time.monotonic()
            self.check(force=True)
            payload = read_packet(
                directory,
                packet,
                self.plan.width,
                self.plan.height,
                directory_identity=directory_identity,
            )
            read_seconds = time.monotonic() - started
            dispatch_started = time.monotonic()
            with background_render_scope():
                pixels = render_packet(
                    payload,
                    width=self.plan.width,
                    height=self.plan.height,
                    deadline=self.deadline,
                )
            dispatch_seconds = time.monotonic() - dispatch_started
            payload = None
            self.check(force=True)
            if pixels is None:
                fallback_started = time.monotonic()
                result = fallback()
                logger.info(
                    "thumbnail_handoff_complete %s",
                    json.dumps(
                        {
                            "event": "thumbnail_handoff_complete",
                            "handoff_id": self.identity,
                            "prepared_read_seconds": read_seconds,
                            "dispatch_seconds": dispatch_seconds,
                            "cpu_fallback_seconds": time.monotonic() - fallback_started,
                            "codec_queue_seconds": 0.0,
                            "codec_seconds": 0.0,
                            "cpu_fallback": True,
                        }
                    ),
                )
                self.check(force=True)
                return result
            # Retained host custody already covers codec RAM. One shared codec
            # slot lets completed frames drain even while a parser holds the
            # whole native memory pool, without an unbounded encoder executor.
            queue_started = time.monotonic()
            with preparation_runtime.reserve_codec(checkpoint=self.check):
                self.check(force=True)
                codec_started = time.monotonic()
                queue_seconds = codec_started - queue_started
                result = encode_pixels(pixels, deadline=self.deadline)
                codec_seconds = time.monotonic() - codec_started
                self.check(force=True)
            logger.info(
                "thumbnail_handoff_complete %s",
                json.dumps(
                    {
                        "event": "thumbnail_handoff_complete",
                        "handoff_id": self.identity,
                        "prepared_read_seconds": read_seconds,
                        "dispatch_seconds": dispatch_seconds,
                        "cpu_fallback_seconds": 0.0,
                        "codec_queue_seconds": queue_seconds,
                        "codec_seconds": codec_seconds,
                        "cpu_fallback": False,
                    }
                ),
            )
            self.check(force=True)
            return result
        finally:
            del payload, pixels, result


@contextmanager
def reserve(plan: HandoffPlan, workspace: Path) -> Iterator[ThumbnailHandoff]:
    deadline = time.monotonic() + settings.mesh_worker_timeout_seconds
    handoff = ThumbnailHandoff(plan, workspace, deadline)
    with preparation_runtime.reserve_host(
        plan.request, plan.capacity, checkpoint=handoff.check
    ):
        try:
            handoff.check(force=True)
            yield handoff
        except BaseException as exc:
            # Failed helper frames may retain the immutable packet or readback.
            # Clear locals while credit is held; preserve stack/line information
            # and the original exception, including cancellation semantics.
            pending = [exc]
            seen: set[int] = set()
            while pending:
                error = pending.pop()
                if id(error) in seen:
                    continue
                seen.add(id(error))
                traceback.clear_frames(error.__traceback__)
                if error.__cause__ is not None:
                    pending.append(error.__cause__)
                if error.__context__ is not None:
                    pending.append(error.__context__)
            raise

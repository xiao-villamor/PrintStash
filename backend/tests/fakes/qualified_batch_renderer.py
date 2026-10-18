"""Controlled qualified hardware behind the real compute broker.

Receipts certify this deterministic test device only; no production GPU or
network service is contacted.
"""

from __future__ import annotations

import json
import os
import struct
import time

import numpy as np
from printstash_core.mesh.preview_profile import RASTERIZER_RECIPE
from printstash_core.mesh.render_geometry import PreparedRender

from app.modules.media.compute_geometry import encode, split
from app.modules.media.compute_render import RECIPE
from app.runtime.compute import client
from app.runtime.compute.contracts import (
    ComputeMode,
    ComputeUnavailable,
    DeviceInfo,
    Operation,
    Reason,
)
from app.runtime.compute.dispatcher import Dispatcher
from app.runtime.compute.protocol import BinaryRenderRequest, Priority
from app.runtime.compute.qualification import Qualification


class QualifiedBatchRenderer:
    def __init__(self, manager):
        self.owner_busy = manager.Event()
        self.release_owner = manager.Event()
        self.batch_started = manager.Event()
        self.release_batch = manager.Event()
        self.stop = manager.Event()
        self.batches = manager.list()
        self._shared = manager.Namespace()
        self._shared.failure = None
        self._shared.colors = [(180, 60, 30, 255), (30, 150, 210, 255)]
        self._shared.owner_pid = None

    @property
    def failure(self) -> Reason | None:
        return self._shared.failure

    @failure.setter
    def failure(self, value: Reason | None) -> None:
        self._shared.failure = value

    @property
    def colors(self) -> list[tuple[int, int, int, int]]:
        return self._shared.colors

    @colors.setter
    def colors(self, value: list[tuple[int, int, int, int]]) -> None:
        self._shared.colors = value

    @property
    def owner_pid(self) -> int | None:
        return self._shared.owner_pid

    @owner_pid.setter
    def owner_pid(self, value: int) -> None:
        self._shared.owner_pid = value

    def dispatcher(self, root, **kwargs):
        self.owner_pid = os.getpid()
        controls = self

        class TestDevice:
            def destroy(self):
                pass

        class QualifiedExecutor(Dispatcher):
            def __init__(self):
                super().__init__(
                    root, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
                )
                self.mode = ComputeMode.AUTO
                self.device = TestDevice()
                self.device_info = DeviceInfo(
                    identity="e2e-controlled-renderer",
                    name="Deterministic test executor",
                    vendor_id=0,
                    device_id=0,
                    driver="test",
                    backend="test",
                )
                receipts = [
                    Qualification(
                        device_identity=self.device_info.identity,
                        runtime_identity=self.runtime,
                        operation=Operation.RENDER,
                        recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:{width}x{height}:1:0",
                        minimum_units=1,
                        maximum_units=100_000,
                        maximum_batch_items=2,
                        quality_passed=True,
                        source_to_publication_speedup=1.5,
                        interactive_p95_ratio=1,
                        peak_device_bytes=1024**2,
                        peak_host_bytes=1024**2,
                    ).model_dump(mode="json")
                    for width, height in ((64, 48), (640, 480))
                ]
                (root / "qualification.json").write_text(json.dumps(receipts))

            def status(self, queue_depth=0, queued_bytes=0):
                # This executor implements the finalized RGBA contract in
                # render_many; it has no real optional-runtime GPU objects.
                return (
                    super()
                    .status(queue_depth, queued_bytes)
                    .model_copy(update={"render_postprocess_gpu": True})
                )

            def admit_host(self, deadline):
                self.host_capacity = 1024**3

            def pressure(self):
                return controls.stop.is_set()

            def render_many(self, requests):
                for request in requests:
                    self.qualification(
                        Operation.RENDER, request.recipe, request.units, len(requests)
                    )
                if [request.request_id for request in requests] == ["busy-device"]:
                    controls.owner_busy.set()
                    if not controls.release_owner.wait(60):
                        raise RuntimeError("test_executor_owner_timeout")
                    return [b"released"]
                records, outputs = [], []
                for index, request in enumerate(requests):
                    assert request._decoded is not None
                    prepared, width, height, views, _matte = request._decoded
                    assert prepared.face_count > 0
                    assert len(views) == 1
                    records.append(
                        (
                            request.geometry_key,
                            request.priority,
                            getattr(prepared, "normal_mode", "canonical"),
                        )
                    )
                    frame = np.zeros((height, width, 4), dtype=np.uint8)
                    frame[
                        height // 4 : 3 * height // 4, width // 4 : 3 * width // 4
                    ] = controls.colors[index]
                    pixels = frame.tobytes()
                    outputs.append(
                        b"\x01" + struct.pack("!III", width, height, 1) + pixels
                    )
                controls.batches.append(records)
                controls.batch_started.set()
                if not controls.release_batch.wait(15):
                    raise RuntimeError("test_executor_batch_timeout")
                if controls.failure is not None:
                    raise ComputeUnavailable(controls.failure)
                return outputs

        return QualifiedExecutor()

    def occupy(self):
        """Keep a prior command in flight until two app Jobs are ready."""
        prepared = PreparedRender(
            np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            1,
            lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
            np.arange(3, dtype=np.int64),
            np.tile([0.0, 0.0, 1.0], (3, 1)),
        )
        key, header, body = split(encode(prepared, 64, 48, [None], False))
        request = BinaryRenderRequest(
            deadline=time.monotonic() + 60,
            priority=Priority.INTERACTIVE,
            request_id="busy-device",
            recipe=f"{RECIPE}:{RASTERIZER_RECIPE}:64x48:1:0",
            units=1,
            geometry_key=key,
            geometry_bytes=len(body),
            header=header,
        )
        return client.exchange_render(request, body)


def serve_qualified(root, controls, configuration):
    """Keep broker RSS and fatal process exits inside a real disposable owner."""
    from app.core.config import _overlay
    from app.runtime.compute.broker import serve

    _overlay.update(configuration)
    serve(root, dispatcher_factory=controls.dispatcher)

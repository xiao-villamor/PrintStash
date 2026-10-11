"""Controlled qualified hardware behind the real compute broker.

Receipts certify this deterministic test device only; no production GPU or
network service is contacted.
"""

from __future__ import annotations

import json
import struct
import threading
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
    def __init__(self):
        self.owner_busy = threading.Event()
        self.release_owner = threading.Event()
        self.batch_started = threading.Event()
        self.release_batch = threading.Event()
        self.stop = threading.Event()
        self.batches = []
        self.failure: Reason | None = None
        self.colors = [(180, 60, 30, 255), (30, 150, 210, 255)]

    def dispatcher(self, root, **kwargs):
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
                    pixels = bytes(controls.colors[index]) * width * height
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

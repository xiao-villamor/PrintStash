"""Independent native startup failures do not disable another compute runtime."""

from app.modules.inference import webgpu
from app.runtime.compute import dispatcher
from app.runtime.compute.contracts import ComputeMode, DeviceInfo, Operation, Reason


class Device:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class NativeProviderError(Exception):
    pass


class TestRuntimeIsolation:
    def test_keeps_render_device_when_inference_runtime_fails(
        self, tmp_path, monkeypatch
    ):
        device = Device()
        info = DeviceInfo(
            identity="physical-test-device",
            name="contract",
            vendor_id=1,
            device_id=2,
            driver="contract",
            backend="Vulkan",
        )
        monkeypatch.setattr(dispatcher, "discover", lambda selector: (info, device))

        def unavailable(info):
            raise NativeProviderError()

        monkeypatch.setattr(webgpu, "SessionFactory", unavailable)

        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.AUTO, selector=None, budget_bytes=1024**3
        )
        try:
            owner.start_inference()
            status = owner.status()
            reasons = {
                capability.operation: capability.reason
                for capability in status.capabilities
            }
            assert owner.device is device
            assert not device.destroyed
            assert reasons[Operation.RENDER] is Reason.UNQUALIFIED
            assert reasons[Operation.DENSE] is Reason.DEVICE_FAILED
        finally:
            owner.close()
        assert device.destroyed


class TestRenderStartup:
    def test_preview_does_not_start_inference(self, tmp_path, monkeypatch):
        from app.core.config import _overlay

        _overlay["compute_render_policy"] = "preview"
        device = Device()
        info = DeviceInfo(
            identity="physical",
            name="hardware",
            vendor_id=1,
            device_id=2,
            driver="test",
            backend="OpenGL",
        )
        monkeypatch.setattr(dispatcher, "discover", lambda selector: (info, device))

        def forbidden(info):
            pytest.fail("render startup initialized inference")

        import pytest

        monkeypatch.setattr(webgpu, "SessionFactory", forbidden)
        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.AUTO, selector=None, budget_bytes=1024**3
        )
        try:
            caps = {c.operation: c for c in owner.status().capabilities}
            assert caps[Operation.RENDER].available
            assert caps[Operation.RENDER].reason is Reason.PREVIEW
            assert not caps[Operation.DENSE].available
            assert owner.factory is None
        finally:
            owner.close()


class TestRenderTelemetry:
    def test_counts_frames_separately_from_native_submissions(
        self, tmp_path, monkeypatch
    ):
        import sys

        import numpy as np
        from printstash_core.mesh.render_geometry import PreparedRender

        from app.modules.media.webgpu_render import Renderer
        from tests.fakes.gpu_device import API
        from tests.fakes.gpu_device import Device as GpuDevice

        monkeypatch.setitem(sys.modules, "wgpu", API)
        device = GpuDevice()
        owner = dispatcher.Dispatcher(
            tmp_path, mode=ComputeMode.CPU, selector=None, budget_bytes=1024**3
        )
        renderer = Renderer(device, owner.memory)
        owner.renderer = renderer
        faces = np.asarray([[0, 1, 2]], dtype=np.int64)
        prepared = PreparedRender(
            np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            1,
            lambda size: iter([faces]),
            np.arange(3, dtype=np.int64),
            np.tile([0.0, 0.0, 1.0], (3, 1)),
        )
        try:
            renderer.execute_prepared_many(
                [
                    (prepared, 24, 24, [None, np.eye(3)], False),
                    (prepared, 24, 24, [None], False),
                ],
                ["geometry:a", "geometry:b"],
                ["a", "b"],
                raw=True,
            )
            status = owner.status()
            assert status.render_frames == 3
            assert status.render_submissions == status.render_batches == 1
            assert status.render_upload_calls == device.mapped_writes == 1
            assert status.render_bounds_vertices == 6
            assert status.geometry_packing_min_faces is None
            assert status.geometry_local_normal_fallback_vertices == 0
            assert status.geometry_cpu_normal_fallbacks == 0
        finally:
            renderer.close()
            owner.close()

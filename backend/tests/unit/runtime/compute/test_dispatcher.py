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

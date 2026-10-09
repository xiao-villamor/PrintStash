"""Only actual discrete or integrated graphics qualify as GPU adapters."""

import pytest

from app.runtime.compute.discovery import identity, physical_adapter


class TestPhysicalAdapter:
    @pytest.mark.parametrize(
        "kind", ["CPU", "Unknown", "Other"], ids=["software", "unknown", "other"]
    )
    def test_rejects_non_gpu_adapters(self, kind):
        assert not physical_adapter({"adapter_type": kind})

    @pytest.mark.parametrize(
        "kind", ["DiscreteGPU", "IntegratedGPU"], ids=["discrete", "integrated"]
    )
    def test_recognizes_hardware_adapters(self, kind):
        assert physical_adapter({"adapter_type": kind})


class TestIdentity:
    def test_changes_when_the_driver_changes(self):
        info = {
            "device": "contract",
            "vendor_id": 1,
            "device_id": 2,
            "description": "driver-a",
            "backend_type": "Vulkan",
        }

        before = identity(info)
        after = identity({**info, "description": "driver-b"})

        assert before.identity != after.identity


class TestDiscovery:
    def test_refuses_software_before_device_creation(self, monkeypatch):
        import sys
        from types import SimpleNamespace

        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.discovery import discover

        adapter = SimpleNamespace(info={"adapter_type": "CPU"})
        monkeypatch.setitem(
            sys.modules,
            "wgpu",
            SimpleNamespace(
                gpu=SimpleNamespace(enumerate_adapters_sync=lambda: [adapter])
            ),
        )

        with pytest.raises(ComputeUnavailable) as exc:
            discover()

        assert exc.value.reason == Reason.SOFTWARE_ADAPTER

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

        from app.runtime.compute import discovery
        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.discovery import discover

        monkeypatch.setattr(discovery, "configure_backend", lambda: None)
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


class TestBackendSelection:
    @pytest.mark.parametrize(
        "backend,wsl,expected",
        [
            ("auto", False, None),
            ("auto", True, "GL"),
            ("vulkan", True, "Vulkan"),
            ("opengl", False, "GL"),
        ],
        ids=["native-auto", "wsl-auto", "explicit-vulkan", "explicit-gl"],
    )
    def test_selects_the_deployment_backend(self, monkeypatch, backend, wsl, expected):
        import sys
        from types import SimpleNamespace

        from app.core.config import _overlay
        from app.runtime.compute import discovery

        _overlay["compute_backend"] = backend
        monkeypatch.setattr(discovery.Path, "exists", lambda path: wsl)
        selected = []
        monkeypatch.setitem(
            sys.modules,
            "wgpu.backends.wgpu_native.extras",
            SimpleNamespace(set_instance_extras=lambda **kw: selected.append(kw)),
        )
        discovery.configure_backend()
        assert selected == ([] if expected is None else [{"backends": [expected]}])

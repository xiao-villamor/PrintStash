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


class TestRuntimeIdentity:
    @pytest.mark.parametrize("source_name", ["gpu_geometry.py", "gpu_normals.py"])
    def test_geometry_shader_changes_invalidate_qualification(
        self, monkeypatch, source_name
    ):
        from pathlib import Path

        from app.runtime.compute.discovery import runtime_identity

        before = runtime_identity()
        original = Path.read_bytes

        def changed_source(path):
            data = original(path)
            if path.name == source_name:
                return data + b"\n# changed geometry shader implementation\n"
            return data

        monkeypatch.setattr(Path, "read_bytes", changed_source)

        assert runtime_identity() != before


class TestProcessIdentity:
    def test_reuses_the_deployed_runtime_identity(self, monkeypatch):
        from app.runtime.compute import discovery

        discovery._process_identity.cache_clear()
        expected = discovery.process_identity()

        def unavailable():
            raise OSError("deployed source is unavailable")

        monkeypatch.setattr(discovery, "runtime_identity", unavailable)
        try:
            assert discovery.process_identity() == expected
        finally:
            discovery._process_identity.cache_clear()

    def test_rechecks_identity_in_a_new_process(self, monkeypatch):
        from app.runtime.compute import discovery

        discovery._process_identity.cache_clear()
        original = discovery.process_identity()
        monkeypatch.setattr(discovery.os, "getpid", lambda: -1)
        monkeypatch.setattr(discovery, "runtime_identity", lambda: "new-deployment")
        try:
            assert discovery.process_identity() == "new-deployment"
            assert original != "new-deployment"
        finally:
            discovery._process_identity.cache_clear()

    def test_retains_mutable_policy_identity(self):
        from app.core.config import _overlay
        from app.runtime.compute.profile import identity

        original = identity()
        _overlay["compute_memory_mb"] = 1537

        assert identity() != original


class TestNativeDiscoveryBoundary:
    @pytest.mark.parametrize(
        "failure", [None, "selector", "canary", "native", "missing"]
    )
    def test_preserves_adapter_admission_outcome(self, monkeypatch, failure):
        import struct
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock

        from app.runtime.compute import discovery
        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from tests.fakes.gpu_device import API, Device

        device = Device()
        device.destroy = Mock()
        device.queue.read_buffer = lambda buffer: struct.pack(
            "<I", 0 if failure == "canary" else 42
        )
        info = {
            "adapter_type": "DiscreteGPU",
            "device": "contract",
            "vendor_id": 1,
            "device_id": 2,
            "description": "driver",
            "backend_type": "Vulkan",
        }
        adapter = SimpleNamespace(info=info, request_device_sync=lambda: device)
        api = SimpleNamespace(
            BufferUsage=API.BufferUsage,
            gpu=SimpleNamespace(enumerate_adapters_sync=Mock(return_value=[adapter])),
        )
        if failure == "native":
            api.gpu.enumerate_adapters_sync.side_effect = OSError("device lost")
        monkeypatch.setattr(discovery, "configure_backend", lambda: None)
        monkeypatch.setitem(sys.modules, "wgpu", None if failure == "missing" else api)
        if failure is None:
            identified, selected = discovery.discover("contract")
            assert selected is device
            assert identified.name == "contract"
            assert all(buffer.destroyed for buffer in device.buffers)
        else:
            with pytest.raises(ComputeUnavailable) as exc:
                discovery.discover("absent" if failure == "selector" else None)
            assert (
                exc.value.reason
                is {
                    "selector": Reason.ADAPTER_MISSING,
                    "canary": Reason.DEVICE_FAILED,
                    "native": Reason.DEVICE_FAILED,
                    "missing": Reason.RUNTIME_MISSING,
                }[failure]
            )
            assert device.destroy.called is (failure == "canary")

    def test_uses_independent_wsl_hardware_evidence(self, monkeypatch):
        from app.runtime.compute import discovery, dxcore

        info = {"adapter_type": "Unknown", "backend_type": "OpenGL"}
        monkeypatch.setattr(discovery.Path, "exists", lambda path: True)
        monkeypatch.setattr(dxcore, "hardware", lambda: ("host evidence",))
        monkeypatch.setattr(
            dxcore, "identify", lambda value, records: {**value, "evidence": records}
        )
        assert discovery.adapter_evidence(info)["evidence"] == ("host evidence",)

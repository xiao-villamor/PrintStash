"""Unknown GL adapters require independent, vendor-neutral hardware evidence."""

import pytest

from app.runtime.compute.dxcore import Hardware, identify


@pytest.fixture
def gl_info():
    return {
        "adapter_type": "Unknown",
        "backend_type": "OpenGL",
        "device": "D3D12 (Hardware)",
        "description": "Mesa",
    }


class TestIdentify:
    @pytest.mark.parametrize(
        "integrated", [False, True], ids=["discrete", "integrated"]
    )
    def test_recognizes_verified_hardware(self, gl_info, integrated):
        info = identify(gl_info, (Hardware("Hardware", 123, 456, 789, integrated),))
        assert info["adapter_type"] == (
            "IntegratedGPU" if integrated else "DiscreteGPU"
        )
        assert (info["vendor_id"], info["device_id"]) == (123, 456)
        assert "789" in info["description"]

    @pytest.mark.parametrize(
        "names",
        [(), ("Other",), ("Hardware", "Hardware")],
        ids=["unverified", "mismatch", "ambiguous"],
    )
    def test_refuses_unproven_hardware(self, gl_info, names):
        records = tuple(Hardware(name, 123, 456, 789, False) for name in names)
        assert identify(gl_info, records)["adapter_type"] == "Unknown"

    @pytest.mark.parametrize(
        "name",
        ["llvmpipe", "Microsoft Basic Render Driver", "WARP"],
        ids=["llvmpipe", "basic", "warp"],
    )
    def test_does_not_promote_software(self, gl_info, name):
        info = {**gl_info, "device": f"D3D12 ({name})"}
        assert (
            identify(info, (Hardware("Hardware", 123, 456, 789, False),))[
                "adapter_type"
            ]
            == "Unknown"
        )

    def test_does_not_override_native_classification(self, gl_info):
        info = {**gl_info, "adapter_type": "CPU"}
        assert identify(info, (Hardware("Hardware", 123, 456, 789, False),)) == info


class TestUnavailableLibrary:
    def test_requires_available_host_hardware_evidence(self, monkeypatch):
        from app.runtime.compute import dxcore

        def missing(_):
            raise OSError("host library unavailable")

        monkeypatch.setattr(dxcore.c, "CDLL", missing)
        assert dxcore.hardware() == ()

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


class TestHostLibraryBoundary:
    @pytest.mark.parametrize(
        "fault", [None, "unsupported", "software", "oversized", "name", "hresult"]
    )
    def test_reads_bounded_hardware_evidence(self, monkeypatch, fault):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from app.runtime.compute import dxcore

        c = dxcore.c
        released = []

        def pointer(destination, value):
            c.cast(destination, c.POINTER(c.c_void_p))[0] = value

        create = Mock(
            side_effect=lambda guid, out: (
                pointer(out, 1),
                -1 if fault == "hresult" else 0,
            )[1]
        )
        monkeypatch.setattr(
            c, "CDLL", lambda name: SimpleNamespace(DXCoreCreateAdapterFactory=create)
        )

        def method(owner, slot, result, *arguments):
            if slot == 2:
                return lambda owner: released.append(owner.value)
            if owner.value == 1:
                return lambda owner, count, attr, iid, out: (pointer(out, 2), 0)[1]
            if owner.value == 2 and slot == 4:
                return lambda owner: 65 if fault == "oversized" else 1
            if owner.value == 2:
                return lambda owner, index, iid, out: (pointer(out, 3), 0)[1]
            if slot == 3:
                return lambda owner: fault != "unsupported"
            if slot == 7:

                def length(owner, key, out):
                    c.cast(out, c.POINTER(c.c_size_t))[0] = 0 if fault == "name" else 9
                    return 0

                return length

            def property_value(owner, key, size, out):
                if key == 2:
                    c.memmove(out, b"Hardware\0", 9)
                elif key == 3:
                    values = c.cast(out, c.POINTER(c.c_uint32))
                    values[0], values[1] = 123, 456
                elif key in (11, 12):
                    c.cast(out, c.POINTER(c.c_bool))[0] = fault != "software"
                elif key == 1:
                    c.cast(out, c.POINTER(c.c_uint64))[0] = 789
                return 0

            return property_value

        monkeypatch.setattr(dxcore, "_method", method)
        result = dxcore.hardware()
        assert result == (() if fault else (Hardware("Hardware", 123, 456, 789, True),))
        assert 1 in released
        if fault is None:
            assert released == [3, 2, 1]

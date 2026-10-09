"""Physical adapter discovery and a real compute canary in the broker only."""

import hashlib
import importlib.metadata
import json
import struct
from pathlib import Path

from .contracts import ComputeUnavailable, DeviceInfo, Reason


def runtime_identity() -> str:
    versions = {}
    for package in (
        "wgpu",
        "onnxruntime",
        "onnxruntime-ep-webgpu",
        "numpy",
        "Pillow",
        "tokenizers",
        "printstash-core",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    sources = hashlib.sha256()
    root = Path(__file__).resolve().parents[2]
    for name in (
        "runtime/compute",
        "modules/inference",
        "modules/media/webgpu_render.py",
        "modules/media/compute_geometry.py",
    ):
        path = root / name
        for source in sorted(path.glob("*.py")) if path.is_dir() else [path]:
            sources.update(source.relative_to(root).as_posix().encode())
            sources.update(source.read_bytes())
    versions["implementation"] = sources.hexdigest()
    return hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()


def physical_adapter(info: dict) -> bool:
    return info.get("adapter_type") in ("DiscreteGPU", "IntegratedGPU")


def adapter_evidence(info: dict) -> dict:
    if (
        info.get("adapter_type") == "Unknown"
        and info.get("backend_type") == "OpenGL"
        and Path("/dev/dxg").exists()
    ):
        from .dxcore import hardware, identify

        return identify(info, hardware())
    return info


def configure_backend() -> None:
    from app.core.config import settings

    backend = settings.compute_backend
    if backend == "auto" and not Path("/dev/dxg").exists():
        return
    from wgpu.backends.wgpu_native.extras import set_instance_extras

    # WSL's maintained Mesa D3D12 GL driver avoids Vulkan/Dozen restrictions.
    # No vendor selection, patched driver or relaxed WebGPU validation.
    set_instance_extras(backends=["Vulkan" if backend == "vulkan" else "GL"])


def discover(selector: str | None = None):
    try:
        import wgpu
    except ImportError:
        raise ComputeUnavailable(Reason.RUNTIME_MISSING) from None
    try:
        configure_backend()
        adapters = wgpu.gpu.enumerate_adapters_sync()
        candidates = [
            (adapter, adapter_evidence(dict(adapter.info))) for adapter in adapters
        ]
        hardware = [
            (adapter, info) for adapter, info in candidates if physical_adapter(info)
        ]
        if not hardware:
            raise ComputeUnavailable(
                Reason.SOFTWARE_ADAPTER if adapters else Reason.ADAPTER_MISSING
            )
        if selector is not None:
            hardware = [
                (adapter, info)
                for adapter, info in hardware
                if selector in (str(info["device"]), identity(info).identity)
            ]
        if not hardware:
            raise ComputeUnavailable(Reason.ADAPTER_MISSING)
        hardware.sort(
            key=lambda item: (
                item[1]["adapter_type"] != "DiscreteGPU",
                identity(item[1]).identity,
            )
        )
        adapter, info = hardware[0]
        device = adapter.request_device_sync()
        try:
            canary(device)
        except RuntimeError, ValueError:
            device.destroy()
            raise ComputeUnavailable(Reason.DEVICE_FAILED) from None
        return identity(info), device
    except ComputeUnavailable:
        raise
    except RuntimeError, OSError:
        raise ComputeUnavailable(Reason.DEVICE_FAILED) from None


def identity(info: dict) -> DeviceInfo:
    data = {
        "name": str(info["device"]),
        "vendor_id": int(info["vendor_id"]),
        "device_id": int(info["device_id"]),
        "driver": str(info["description"]),
        "backend": str(info["backend_type"]),
    }
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return DeviceInfo(identity=digest, **data)


def canary(device) -> None:
    import wgpu

    buffer = device.create_buffer_with_data(
        data=struct.pack("<I", 7),
        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
    )
    try:
        shader = device.create_shader_module(
            code="""
            @group(0) @binding(0) var<storage, read_write> data: array<u32>;
            @compute @workgroup_size(1) fn main() { data[0] = data[0] * 6u; }
        """
        )
        pipeline = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "main"}
        )
        bind = device.create_bind_group(
            layout=pipeline.get_bind_group_layout(0),
            entries=[{"binding": 0, "resource": {"buffer": buffer}}],
        )
        encoder = device.create_command_encoder()
        compute = encoder.begin_compute_pass()
        compute.set_pipeline(pipeline)
        compute.set_bind_group(0, bind)
        compute.dispatch_workgroups(1)
        compute.end()
        device.queue.submit([encoder.finish()])
        if bytes(device.queue.read_buffer(buffer)) != struct.pack("<I", 42):
            raise ValueError("compute_canary_failed")
    finally:
        buffer.destroy()

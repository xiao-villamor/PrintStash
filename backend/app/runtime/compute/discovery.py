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


def discover(selector: str | None = None):
    try:
        import wgpu
    except ImportError:
        raise ComputeUnavailable(Reason.RUNTIME_MISSING) from None
    try:
        adapters = wgpu.gpu.enumerate_adapters_sync()
        hardware = [
            adapter for adapter in adapters if physical_adapter(dict(adapter.info))
        ]
        if not hardware:
            raise ComputeUnavailable(
                Reason.SOFTWARE_ADAPTER if adapters else Reason.ADAPTER_MISSING
            )
        if selector is not None:
            hardware = [
                adapter
                for adapter in hardware
                if selector
                in (str(adapter.info["device"]), identity(dict(adapter.info)).identity)
            ]
        if not hardware:
            raise ComputeUnavailable(Reason.ADAPTER_MISSING)
        # Stable preference without vendor-specific implementations.
        hardware.sort(
            key=lambda a: (
                a.info["adapter_type"] != "DiscreteGPU",
                identity(dict(a.info)).identity,
            )
        )
        adapter = hardware[0]
        device = adapter.request_device_sync()
        try:
            canary(device)
        except RuntimeError, ValueError:
            device.destroy()
            raise ComputeUnavailable(Reason.DEVICE_FAILED) from None
        return identity(dict(adapter.info)), device
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

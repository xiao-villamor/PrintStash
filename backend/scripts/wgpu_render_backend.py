"""Bounded WebGPU qualification adapter; deliberately absent from production Jobs.

The GPU rasterizes canonical screen coordinates and interpolated normals. The
existing shade callback owns lighting/material policy, including matte views.
Only one normal/coverage readback is performed per complete frame. Requested
storage includes attachments, the bounded upload and aligned staging storage;
this is allocation accounting, not a measurement of physical VRAM.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Buffer, Callable, Mapping
from contextlib import ExitStack
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from scripts.render_backend import FloatArray, GpuError, GpuFailure, GpuStats, Shade

if TYPE_CHECKING:
    import wgpu


_SHADER = """
struct Dimensions { width: f32, height: f32, radius: f32, padding: f32 };
@group(0) @binding(0) var<uniform> dimensions: Dimensions;
struct Vertex {
    @builtin(position) position: vec4f,
    @location(0) normal: vec3f,
};
@vertex fn vertex(@location(0) position: vec3f, @location(1) normal: vec3f) -> Vertex {
    var result: Vertex;
    result.position = vec4f(2.0 * position.x / dimensions.width - 1.0,
                           1.0 - 2.0 * position.y / dimensions.height,
                           (position.z / dimensions.radius + 1.0) * 0.5, 1.0);
    result.normal = normal;
    return result;
}
@fragment fn fragment(input: Vertex) -> @location(0) vec4f {
    return vec4f(input.normal, 1.0);
}
"""


class NativeFrame(Protocol):
    def upload(self, payload: bytes) -> None: ...
    def draw(self, vertices: int) -> None: ...
    def read(self) -> bytes: ...
    def close(self) -> None: ...


class NativeDevice(Protocol):
    @property
    def info(self) -> Mapping[str, str | int | bool]: ...

    @property
    def limits(self) -> Mapping[str, int]: ...

    def frame(
        self, width: int, height: int, chunk: int, radius: float
    ) -> NativeFrame: ...
    def close(self) -> None: ...


def is_hardware(info: Mapping[str, object]) -> bool:
    """Adapter type, never vendor identity, determines acceleration claims."""
    return info["adapter_type"] in ("DiscreteGPU", "IntegratedGPU")


def _native_device(selector: str | None, allow_software: bool) -> NativeDevice:
    # Optional import: CPU installations never need to load native GPU libraries.
    import wgpu

    adapters = wgpu.gpu.enumerate_adapters_sync()
    eligible = [
        adapter
        for adapter in adapters
        if (allow_software or is_hardware(adapter.info))
        and adapter.limits["max-color-attachment-bytes-per-sample"] >= 16
        and adapter.limits["max-vertex-attributes"] >= 2
    ]
    if selector is not None:
        eligible = [a for a in eligible if a.info["device"] == selector]
        if len(eligible) != 1:
            raise GpuError(GpuFailure.CAPABILITY_UNAVAILABLE)
    if not eligible:
        raise GpuError(GpuFailure.CAPABILITY_UNAVAILABLE)
    adapter = eligible[0]
    device = adapter.request_device_sync(
        required_limits={"max-color-attachment-bytes-per-sample": 16}
    )
    try:
        return _Device(device, dict(adapter.info))
    except BaseException:
        device.destroy()
        raise


class _Device:
    def __init__(self, device: wgpu.GPUDevice, identity: dict[str, str | int | bool]):
        self.device, self.info = device, identity
        self.info["physical_acceleration"] = is_hardware(identity)
        self.limits = device.limits
        # One pipeline per session. No material or lighting constants in WGSL.
        try:
            shader = device.create_shader_module(code=_SHADER)
            self.pipeline = device.create_render_pipeline(
                layout="auto",
                vertex={
                    "module": shader,
                    "entry_point": "vertex",
                    "buffers": [
                        {
                            "array_stride": 24,
                            "step_mode": "vertex",
                            "attributes": [
                                {
                                    "format": "float32x3",
                                    "offset": 0,
                                    "shader_location": 0,
                                },
                                {
                                    "format": "float32x3",
                                    "offset": 12,
                                    "shader_location": 1,
                                },
                            ],
                        }
                    ],
                },
                primitive={"topology": "triangle-list", "cull_mode": "none"},
                depth_stencil={
                    "format": "depth32float",
                    "depth_write_enabled": True,
                    "depth_compare": "less",
                },
                fragment={
                    "module": shader,
                    "entry_point": "fragment",
                    "targets": [{"format": "rgba32float"}],
                },
            )
        except Exception as exc:
            # This is the native boundary: normalize backend-specific exceptions.
            raise GpuError(GpuFailure.SHADER_FAILED) from exc

    def frame(self, width: int, height: int, chunk: int, radius: float) -> NativeFrame:
        return _Frame(self, width, height, chunk, radius)

    def close(self) -> None:
        self.device.destroy()


class _Frame:
    def __init__(
        self, owner: _Device, width: int, height: int, chunk: int, radius: float
    ):
        import wgpu

        self.owner, self.width, self.height = owner, width, height
        self.resources = ExitStack()
        self.first = True
        device = owner.device
        try:
            self.color = device.create_texture(
                size=(width, height, 1),
                format="rgba32float",
                usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
            )
            self.resources.callback(self.color.destroy)
            self.depth = device.create_texture(
                size=(width, height, 1),
                format="depth32float",
                usage=wgpu.TextureUsage.RENDER_ATTACHMENT,
            )
            self.resources.callback(self.depth.destroy)
            self.vertices = device.create_buffer(
                size=chunk * 72,
                usage=wgpu.BufferUsage.VERTEX | wgpu.BufferUsage.COPY_DST,
            )
            self.resources.callback(self.vertices.destroy)
            self.uniform = device.create_buffer(
                size=16,
                usage=wgpu.BufferUsage.UNIFORM
                | wgpu.BufferUsage.COPY_DST
                | wgpu.BufferUsage.COPY_SRC,
            )
            self.resources.callback(self.uniform.destroy)
            self.stride = ((width * 16 + 255) // 256) * 256
            self.staging = device.create_buffer(
                size=self.stride * height,
                usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ,
            )
            self.resources.callback(self.staging.destroy)
            device.queue.write_buffer(
                self.uniform,
                0,
                np.array([width, height, radius, 0], dtype=np.float32),
            )
            self.binding = device.create_bind_group(
                layout=owner.pipeline.get_bind_group_layout(0),
                entries=[{"binding": 0, "resource": {"buffer": self.uniform}}],
            )
            # Clear even an empty frame before readback.
            self.draw(0)
        except BaseException:
            self.resources.close()
            raise

    def upload(self, payload: bytes) -> None:
        self.owner.device.queue.write_buffer(self.vertices, 0, payload)

    def draw(self, vertices: int) -> None:
        device = self.owner.device
        encoder = device.create_command_encoder()
        render = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": self.color.create_view(),
                    "load_op": "clear" if self.first else "load",
                    "store_op": "store",
                    "clear_value": (0, 0, 0, 0),
                }
            ],
            depth_stencil_attachment={
                "view": self.depth.create_view(),
                "depth_load_op": "clear" if self.first else "load",
                "depth_store_op": "store",
                "depth_clear_value": 1.0,
            },
        )
        render.set_pipeline(self.owner.pipeline)
        render.set_bind_group(0, self.binding)
        render.set_vertex_buffer(0, self.vertices)
        render.draw(vertices)
        render.end()
        encoder.copy_buffer_to_buffer(self.uniform, 0, self.staging, 0, 4)
        device.queue.submit([encoder.finish()])
        # Mapping a queued copy waits for completion through the public API.
        # wgpu 0.32.0 queue-completion callbacks have an upstream ABI defect.
        self.staging.map_sync("READ", size=4)
        self.staging.unmap()
        self.first = False

    def read(self) -> bytes:
        device = self.owner.device
        encoder = device.create_command_encoder()
        encoder.copy_texture_to_buffer(
            {"texture": self.color},
            {
                "buffer": self.staging,
                "bytes_per_row": self.stride,
                "rows_per_image": self.height,
            },
            (self.width, self.height, 1),
        )
        device.queue.submit([encoder.finish()])
        self.staging.map_sync("READ")
        try:
            # wgpu types this as ArrayLike; mapped data supports the buffer
            # protocol consumed by NumPy without an additional copy.
            payload = cast(Buffer, self.staging.read_mapped())
            rows = np.frombuffer(payload, dtype=np.uint8).reshape(
                self.height, self.stride
            )
            return rows[:, : self.width * 16].tobytes()
        finally:
            self.staging.unmap()

    def close(self) -> None:
        self.resources.close()


class GpuContext:
    def __init__(self, native: NativeDevice):
        self.native = native
        self.info = dict(native.info)
        self.info["physical_acceleration"] = is_hardware(native.info)
        self._owner = threading.get_ident()
        self._closed = False
        self._frame: GpuFrame | None = None

    @classmethod
    def create(
        cls,
        *,
        backend: str = "auto",
        selector: str | None = None,
        allow_software: bool = False,
        context_factory: Callable[[str | None, bool], NativeDevice] | None = None,
    ) -> GpuContext:
        if backend != "auto" or type(allow_software) is not bool:
            raise GpuError(GpuFailure.INVALID_REQUEST)
        native = None
        try:
            native = (context_factory or _native_device)(selector, allow_software)
            return cls(native)
        except BaseException as exc:
            if native is not None:
                native.close()
            if isinstance(exc, GpuError):
                raise
            if isinstance(exc, Exception):
                raise GpuError(
                    GpuFailure.DEPENDENCY_UNAVAILABLE
                    if isinstance(exc, ImportError)
                    else GpuFailure.CONTEXT_FAILED
                ) from exc
            raise

    def require_open(self) -> None:
        if threading.get_ident() != self._owner or self._closed:
            raise GpuError(GpuFailure.CLOSED)

    def close(self) -> None:
        if self._closed:
            return
        self.require_open()
        try:
            if self._frame is not None:
                self._frame.close()
        finally:
            self._closed = True
            self.native.close()


class GpuFrame:
    def __init__(
        self,
        context: GpuContext,
        width: int,
        height: int,
        face_chunk_size: int,
        depth_radius: float,
        matte: bool = False,
        allocation_limit_bytes: int = 512 * 1024 * 1024,
    ):
        if (
            any(
                type(v) is not int or v < 1
                for v in (
                    width,
                    height,
                    face_chunk_size,
                    allocation_limit_bytes,
                )
            )
            or type(matte) is not bool
            or type(depth_radius) not in (float, int)
            # GPU uniforms must remain finite and nonzero even on devices that
            # flush float32 subnormals to zero.
            or not (
                float(np.finfo(np.float32).tiny)
                <= depth_radius
                <= float(np.finfo(np.float32).max)
            )
        ):
            raise GpuError(GpuFailure.INVALID_REQUEST)
        stride = ((width * 16 + 255) // 256) * 256
        requested = width * height * 20 + stride * height + face_chunk_size * 72 + 16
        if requested > allocation_limit_bytes:
            raise GpuError(GpuFailure.ALLOCATION_FAILED)
        context.require_open()
        if context._frame is not None:
            raise GpuError(GpuFailure.INVALID_REQUEST)
        limits = context.native.limits
        if (
            max(width, height) > limits["max-texture-dimension-2d"]
            or max(face_chunk_size * 72, stride * height) > limits["max-buffer-size"]
        ):
            raise GpuError(GpuFailure.CAPABILITY_UNAVAILABLE)
        self.context, self.width, self.height = context, width, height
        self.face_chunk_size, self.depth_radius = face_chunk_size, depth_radius
        self.stats = GpuStats(requested)
        self.failure: GpuError | None = None
        self._closed = self._finished = False
        self._shade: Shade | None = None
        try:
            self.native = context.native.frame(
                width, height, face_chunk_size, depth_radius
            )
        except GpuError:
            raise
        except Exception as exc:
            raise GpuError(GpuFailure.ALLOCATION_FAILED) from exc
        context._frame = self

    def _require_open(self) -> None:
        self.context.require_open()
        if self._closed or self._finished:
            raise GpuError(GpuFailure.CLOSED)
        if self.failure is not None:
            raise self.failure

    def __call__(
        self,
        img: NDArray[np.uint8],
        zbuf: FloatArray,
        tri: FloatArray,
        vert_nrm: FloatArray,
        shade: Shade,
        base_color: FloatArray,
        width: int,
        height: int,
    ) -> None:
        self._require_open()
        if (
            (width, height) != (self.width, self.height)
            or tri.shape != vert_nrm.shape
            or tri.ndim != 3
            or tri.shape[1:] != (3, 3)
            or len(tri) > self.face_chunk_size
            or not np.isfinite(tri).all()
            or not np.isfinite(vert_nrm).all()
            or not np.all(np.asarray(base_color) == 255)
            or (tri.size > 0 and np.max(np.abs(tri[:, :, 2])) >= self.depth_radius)
        ):
            self.failure = GpuError(GpuFailure.INVALID_REQUEST)
            raise self.failure
        if not len(tri):
            return
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        denominator = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (
            c[:, 0] - b[:, 0]
        ) * (a[:, 1] - c[:, 1])
        selected = np.abs(denominator) > 1e-9
        if not selected.any():
            return
        tri, vert_nrm = tri[selected], vert_nrm[selected]
        self._shade = shade
        packed = np.empty((len(tri) * 3, 6), dtype=np.float32)
        packed[:, :3] = tri.reshape(-1, 3)
        packed[:, 3:] = vert_nrm.reshape(-1, 3)
        if not np.isfinite(packed).all():
            self.failure = GpuError(GpuFailure.INVALID_REQUEST)
            raise self.failure
        started = time.perf_counter_ns()
        try:
            self.native.upload(packed.tobytes())
            self.stats.upload_bytes += packed.nbytes
            self.stats.upload_ms += (time.perf_counter_ns() - started) / 1e6
            started = time.perf_counter_ns()
            self.native.draw(len(packed))
            self.stats.draw_calls += 1
            self.stats.draw_ms += (time.perf_counter_ns() - started) / 1e6
        except Exception as exc:
            self.failure = (
                exc if isinstance(exc, GpuError) else GpuError(GpuFailure.DRAW_FAILED)
            )
            raise self.failure from exc

    def finish(self, img: NDArray[np.uint8], zbuf: FloatArray) -> None:
        self._require_open()
        if (
            img.shape != (self.height, self.width, 3)
            or img.dtype != np.uint8
            or zbuf.shape != (self.height, self.width)
            or zbuf.dtype.kind != "f"
        ):
            self.failure = GpuError(GpuFailure.INVALID_REQUEST)
            raise self.failure
        started = time.perf_counter_ns()
        try:
            payload = self.native.read()
            self.stats.readback_count += 1
            if len(payload) != self.width * self.height * 16:
                raise ValueError("incomplete normal readback")
            rgba = np.frombuffer(payload, dtype=np.float32).reshape(
                self.height, self.width, 4
            )
            if not np.isfinite(rgba).all() or not np.isin(rgba[:, :, 3], [0, 1]).all():
                raise ValueError("invalid normal readback")
            mask = rgba[:, :, 3] == 1
            result = np.zeros_like(img)
            if mask.any():
                if self._shade is None:
                    raise ValueError("coverage without submitted geometry")
                normals = rgba[:, :, :3][mask].astype(np.float64)
                length = np.linalg.norm(normals, axis=1, keepdims=True)
                normals /= np.where(length == 0, 1, length)
                shaded = self._shade(normals)
                if shaded.shape != normals.shape or not np.isfinite(shaded).all():
                    raise ValueError("invalid canonical shading result")
                result[mask] = (np.clip(shaded, 0, 1) * 255).astype(np.uint8)
            # Publish only after complete readback and canonical shading succeed.
            img[:] = result
            zbuf[:] = np.where(mask, 0.0, np.inf)
            self._finished = True
        except Exception as exc:
            self.failure = (
                exc
                if isinstance(exc, GpuError)
                else GpuError(GpuFailure.READBACK_FAILED)
            )
            raise self.failure from exc
        finally:
            self.stats.readback_ms += (time.perf_counter_ns() - started) / 1e6

    def close(self) -> None:
        if self._closed:
            return
        self.context.require_open()
        self._closed = True
        try:
            self.native.close()
        finally:
            self.context._frame = None

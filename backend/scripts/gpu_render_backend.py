"""Disposable ModernGL experiment for the canonical CPU-prepared raster stream.

Only the canonical preview lighting callback is supported. CPU code retains
projection, visibility, crease normals and final image processing. Allocation
figures are nominal requested buffer/attachment storage (four bytes per depth
texel), not measured physical VRAM.
"""

from __future__ import annotations

import importlib
import math
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE


class GpuFailure(StrEnum):
    INVALID_REQUEST = "invalid_request"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CONTEXT_FAILED = "context_failed"
    SHADER_FAILED = "shader_failed"
    ALLOCATION_FAILED = "allocation_failed"
    DRAW_FAILED = "draw_failed"
    READBACK_FAILED = "readback_failed"
    CLOSED = "closed"


class GpuError(Exception):
    def __init__(self, reason: GpuFailure):
        self.reason = reason
        super().__init__(reason.value)


class NativeResource(Protocol):
    def release(self) -> None: ...


class NativeBuffer(NativeResource, Protocol):
    def write(self, data: bytes) -> None: ...


class NativeUniform(Protocol):
    value: object


class NativeProgram(NativeResource, Protocol):
    def __getitem__(self, name: str) -> NativeUniform: ...


class NativeVertexArray(NativeResource, Protocol):
    def render(self, *, vertices: int) -> None: ...


class NativeFramebuffer(NativeResource, Protocol):
    viewport: tuple[int, int, int, int]

    def use(self) -> None: ...
    def clear(
        self, red: float, green: float, blue: float, alpha: float, *, depth: float
    ) -> None: ...
    def read(self, *, components: int, alignment: int) -> bytes: ...


class NativeContext(NativeResource, Protocol):
    info: Mapping[str, object]
    version_code: int
    depth_func: str
    depth_mask: bool

    def texture(
        self, size: tuple[int, int], components: int, *, dtype: str
    ) -> NativeResource: ...
    def depth_texture(self, size: tuple[int, int]) -> NativeResource: ...
    def framebuffer(
        self,
        *,
        color_attachments: tuple[NativeResource, ...],
        depth_attachment: NativeResource,
    ) -> NativeFramebuffer: ...
    def program(self, *, vertex_shader: str, fragment_shader: str) -> NativeProgram: ...
    def buffer(self, *, reserve: int) -> NativeBuffer: ...
    def vertex_array(
        self, program: NativeProgram, content: list[tuple[NativeBuffer, str, str, str]]
    ) -> NativeVertexArray: ...
    def enable_only(self, flags: int) -> None: ...
    def finish(self) -> None: ...


@dataclass
class GpuStats:
    requested_allocation_bytes: int
    upload_bytes: int = 0
    draw_calls: int = 0
    readback_count: int = 0
    upload_ms: float = 0.0
    draw_ms: float = 0.0
    readback_ms: float = 0.0


MODERNGL_DEPTH_TEST = 2


_VERTEX_SHADER = """#version 330
in vec3 position;
in vec3 normal;
out vec3 interpolated_normal;
uniform vec2 dimensions;
uniform float depth_radius;
void main() {
    gl_Position = vec4(2.0 * position.x / dimensions.x - 1.0,
                       1.0 - 2.0 * position.y / dimensions.y,
                       position.z / depth_radius, 1.0);
    interpolated_normal = normal;
}
"""
_FRAGMENT_SHADER = """#version 330
in vec3 interpolated_normal;
out vec4 fragment;
uniform vec3 albedo;
uniform float specular_strength;
uniform int silhouette;
void main() {
    vec3 rgb;
    if (silhouette == 1) {
        rgb = albedo * 0.6;
    } else {
        float magnitude = length(interpolated_normal);
        vec3 n = magnitude == 0.0 ? vec3(0.0) : interpolated_normal / magnitude;
        vec3 key = normalize(vec3(-0.5, 0.65, 1.0));
        vec3 fill = normalize(vec3(0.55, -0.25, 0.55));
        vec3 halfway = normalize(key + vec3(0.0, 0.0, 1.0));
        float dk = clamp(dot(n, key), 0.0, 1.0);
        float df = clamp(dot(n, fill), 0.0, 1.0);
        float fresnel = pow(1.0 - clamp(n.z, 0.0, 1.0), 3.0);
        float specular = pow(clamp(dot(n, halfway), 0.0, 1.0), 32.0);
        rgb = (vec3(0.30) + 1.05 * dk * vec3(1.00, 0.98, 0.95)
               + 0.30 * df * vec3(0.55, 0.62, 0.78)) * albedo
              + 0.22 * fresnel * vec3(0.85, 0.92, 1.00)
              + specular_strength * specular;
    }
    // CPU uint8 conversion truncates; avoid default UNORM rounding changing it.
    fragment = vec4(floor(clamp(rgb, 0.0, 1.0) * 255.0) / 255.0, 1.0);
}
"""


class _NativeModule(Protocol):
    def create_standalone_context(
        self, *, require: int, backend: str
    ) -> NativeContext: ...


def _native_context(backend: str) -> NativeContext:
    module = cast(_NativeModule, importlib.import_module("moderngl"))
    return module.create_standalone_context(require=330, backend=backend)


class GpuContext:
    def __init__(self, native: NativeContext):
        self.native = native
        self._owner_thread = threading.get_ident()
        self._closed = False
        self._frames: set[GpuFrame] = set()
        self._program: NativeProgram | None = None
        self.info: dict[str, str | int] = {}
        for key in ("GL_VENDOR", "GL_RENDERER", "GL_VERSION"):
            value = native.info[key]
            if type(value) is not str or not value:
                raise ValueError("invalid_required_gpu_identity")
            self.info[key] = value
        self.info["version_code"] = native.version_code

    @classmethod
    def create(
        cls,
        *,
        backend: str = "egl",
        context_factory: Callable[[str], NativeContext] | None = None,
    ) -> GpuContext:
        if backend != "egl":
            raise GpuError(GpuFailure.INVALID_REQUEST)
        native = None
        try:
            native = (context_factory or _native_context)(backend)
            return cls(native)
        except BaseException as exc:
            if native is not None:
                try:
                    native.release()
                except BaseException as cleanup:
                    exc.add_note(f"context cleanup failed: {type(cleanup).__name__}")
            if isinstance(exc, Exception):
                raise GpuError(
                    GpuFailure.DEPENDENCY_UNAVAILABLE
                    if isinstance(exc, ImportError)
                    else GpuFailure.CONTEXT_FAILED
                ) from exc
            raise

    def require_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise GpuError(GpuFailure.CLOSED)

    def require_open(self) -> None:
        self.require_owner_thread()
        if self._closed:
            raise GpuError(GpuFailure.CLOSED)

    def attach_frame(self, frame: GpuFrame) -> None:
        """Claim the sole mutable-uniform frame before any native allocation."""
        self.require_open()
        if self._frames or frame.context is not self:
            raise GpuError(GpuFailure.INVALID_REQUEST)
        self._frames.add(frame)

    def detach_frame(self, frame: GpuFrame) -> None:
        self.require_owner_thread()
        self._frames.discard(frame)

    def acquire_program(self) -> NativeProgram:
        """Compile the canonical program once; the context owns its lifetime."""
        self.require_open()
        if self._program is None:
            try:
                self._program = self.native.program(
                    vertex_shader=_VERTEX_SHADER, fragment_shader=_FRAGMENT_SHADER
                )
            except Exception as exc:
                raise GpuError(GpuFailure.SHADER_FAILED) from exc
        return self._program

    def close(self) -> None:
        if self._closed:
            return
        self.require_open()
        self._closed = True
        with ExitStack() as cleanup:
            cleanup.callback(self.native.release)
            if self._program is not None:
                cleanup.callback(self._program.release)
            for frame in tuple(self._frames):
                cleanup.callback(frame.close)


FloatArray = NDArray[np.float32] | NDArray[np.float64]


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
        for value in (width, height, face_chunk_size, allocation_limit_bytes):
            if type(value) is not int or value < 1:
                raise GpuError(GpuFailure.INVALID_REQUEST)
        if (
            type(matte) is not bool
            or type(depth_radius) not in (int, float)
            or not math.isfinite(depth_radius)
            or depth_radius <= 0
        ):
            raise GpuError(GpuFailure.INVALID_REQUEST)
        requested = width * height * 8 + face_chunk_size * 3 * 6 * 4
        if requested > allocation_limit_bytes:
            raise GpuError(GpuFailure.ALLOCATION_FAILED)
        context.require_open()
        self.context, self.width, self.height = context, width, height
        self.face_chunk_size, self.depth_radius = face_chunk_size, depth_radius
        self.stats = GpuStats(requested)
        self.failure: GpuError | None = None
        self._closed = self._finished = False
        self._resources = ExitStack()
        try:
            context.attach_frame(self)
            native = context.native

            def own(resource: NativeResource) -> NativeResource:
                self._resources.callback(resource.release)
                return resource

            color = own(native.texture((width, height), 4, dtype="f1"))
            depth = own(native.depth_texture((width, height)))
            self.framebuffer = native.framebuffer(
                color_attachments=(color,), depth_attachment=depth
            )
            own(self.framebuffer)
            self.program = context.acquire_program()
            self.buffer = native.buffer(reserve=face_chunk_size * 3 * 6 * 4)
            own(self.buffer)
            self.vao = native.vertex_array(
                self.program, [(self.buffer, "3f 3f", "position", "normal")]
            )
            own(self.vao)
            self.program["dimensions"].value = (float(width), float(height))
            self.program["depth_radius"].value = float(depth_radius)
            self.program["albedo"].value = PREVIEW_PROFILE.material_albedo
            self.program["specular_strength"].value = 0.0 if matte else 0.22
            self.framebuffer.viewport = (0, 0, width, height)
            self.framebuffer.use()
            native.enable_only(
                MODERNGL_DEPTH_TEST
            )  # ModernGL DEPTH_TEST bitmask; no culling, blending or sRGB.
            native.depth_func, native.depth_mask = "<", True
            self.framebuffer.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        except BaseException as exc:
            try:
                self._resources.close()
            except BaseException as cleanup:
                exc.add_note(f"frame cleanup failed: {type(cleanup).__name__}")
            self._closed = True
            context.detach_frame(self)
            if isinstance(exc, GpuError):
                raise
            if isinstance(exc, Exception):
                raise GpuError(GpuFailure.ALLOCATION_FAILED) from exc
            raise

    def _refuse(self, reason: GpuFailure) -> NoReturn:
        self.failure = GpuError(reason)
        raise self.failure

    def _require_open(self) -> None:
        # A foreign caller must not poison a frame owned by another thread.
        self.context.require_owner_thread()
        try:
            self.context.require_open()
        except GpuError as exc:
            self.failure = exc
            raise
        if self._closed or self._finished:
            self._refuse(GpuFailure.CLOSED)
        if self.failure is not None:
            raise self.failure

    def __call__(
        self,
        img: NDArray[np.uint8],
        zbuf: FloatArray,
        tri: FloatArray,
        vert_nrm: FloatArray,
        shade: Callable[[FloatArray], FloatArray],
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
        ):
            self._refuse(GpuFailure.INVALID_REQUEST)
        if len(tri) == 0:
            return
        if np.max(np.abs(tri[:, :, 2])) >= self.depth_radius:
            self._refuse(GpuFailure.INVALID_REQUEST)
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        denominator = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (
            c[:, 0] - b[:, 0]
        ) * (a[:, 1] - c[:, 1])
        selected = np.abs(denominator) > 1e-9
        if not selected.any():
            return
        packed = np.empty((int(selected.sum()) * 3, 6), dtype=np.float32)
        packed[:, :3] = tri[selected].reshape((-1, 3))
        packed[:, 3:] = vert_nrm[selected].reshape((-1, 3))
        if not np.isfinite(packed).all():
            self._refuse(GpuFailure.INVALID_REQUEST)
        try:
            self.framebuffer.use()
            self.program["silhouette"].value = int(not np.any(vert_nrm[selected]))
            started = time.perf_counter_ns()
            try:
                self.buffer.write(packed.tobytes())
                self.stats.upload_bytes += packed.nbytes
            finally:
                self.stats.upload_ms += (time.perf_counter_ns() - started) / 1e6
            started = time.perf_counter_ns()
            try:
                self.vao.render(vertices=len(packed))
                self.stats.draw_calls += 1
                self.context.native.finish()  # Drain before reusing the same VBO.
            finally:
                self.stats.draw_ms += (time.perf_counter_ns() - started) / 1e6
        except Exception as exc:
            self.failure = GpuError(GpuFailure.DRAW_FAILED)
            raise self.failure from exc
        return  # No CPU candidate-pixel enumeration statistic exists for GPU draws.

    def finish(self, img: NDArray[np.uint8], zbuf: FloatArray) -> None:
        self._require_open()
        if (
            img.shape != (self.height, self.width, 3)
            or zbuf.shape != (self.height, self.width)
            or img.dtype != np.uint8
            or zbuf.dtype.kind != "f"
        ):
            self._refuse(GpuFailure.INVALID_REQUEST)
        started = time.perf_counter_ns()
        try:
            self.context.native.finish()
            payload = self.framebuffer.read(components=4, alignment=1)
            self.stats.readback_count += 1
            if len(payload) != self.width * self.height * 4:
                raise ValueError("invalid GPU readback length")
            rgba = np.frombuffer(payload, dtype=np.uint8).reshape(
                (self.height, self.width, 4)
            )[::-1]
            img[:] = rgba[:, :, :3]
            zbuf[:] = np.where(rgba[:, :, 3] != 0, 0.0, np.inf)
            self._finished = True
        except Exception as exc:
            self.failure = GpuError(GpuFailure.READBACK_FAILED)
            raise self.failure from exc
        finally:
            self.stats.readback_ms += (time.perf_counter_ns() - started) / 1e6

    def close(self) -> None:
        if self._closed:
            return
        self.context.require_owner_thread()
        self._closed = True
        self.context.detach_frame(self)
        self._resources.close()

"""Visual render inputs, lazy CPU recovery and the legacy guarded CSR provider.

The current visual representation defers welding and normals to the device.
Older CSR-tagged inputs retain their canonical per-item numerical recovery.
Neither representation changes authoritative source geometry or measurements.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from printstash_core.mesh.render_geometry import (
    PreparedRender,
    PreparedRenderGeometry,
    prepare_normals,
    weld_positions,
)

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    IndexArray = NDArray[np.int64] | NDArray[np.uint32]

NORMAL_MODE: Final = "gpu-angle-csr-v1"
GPU_NORMALS_MIN_FACES = 20_000
MAX_DEGREE = 4096
FACE_CHUNK = 64_000
MAX_GEOMETRY_BYTES = 48 * 1024**2
MAX_FACES = 2_000_000
MAX_VERTICES = 6_000_000
# Each edge is bounded by 2C, each cross component by 8C², and its
# squared three-component norm by 192C⁴. Leave margin for rounding.
MAX_ABS_COORDINATE = (3.402823466e38 / 256.0) ** 0.25


@dataclass(frozen=True)
class DeferredRender:
    """Owned render geometry without invented or placeholder smooth normals."""

    geometry: PreparedRenderGeometry
    position_ids: IndexArray
    position_count: int
    normal_mode: ClassVar[Literal["gpu-angle-csr-v1"]] = NORMAL_MODE
    _canonical: PreparedRender | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def vertices(self):
        return self.geometry.vertices

    @property
    def face_count(self):
        return self.geometry.face_count

    @property
    def face_chunks(self):
        return self.geometry.face_chunks


VISUAL_NORMAL_MODE: Final = "gpu-visual-v2"


@dataclass(frozen=True)
class VisualRender:
    """Unwelded visual geometry; the device computes adjacency and smooth normals."""

    geometry: PreparedRenderGeometry
    normal_mode: ClassVar[Literal["gpu-visual-v2"]] = VISUAL_NORMAL_MODE
    _canonical: PreparedRender | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def vertices(self):
        return self.geometry.vertices

    @property
    def face_count(self):
        return self.geometry.face_count

    @property
    def face_chunks(self):
        return self.geometry.face_chunks

    @property
    def position_count(self):
        return len(self.vertices)


RenderInput = PreparedRender | DeferredRender | VisualRender


def defer(geometry: PreparedRenderGeometry) -> DeferredRender:
    identities = weld_positions(geometry)
    return DeferredRender(geometry, identities, int(identities.max()) + 1)


def eligible(prepared: DeferredRender | VisualRender) -> bool:
    n, f, k = len(prepared.vertices), prepared.face_count, prepared.position_count
    return (
        GPU_NORMALS_MIN_FACES <= f <= MAX_FACES
        and 0 < n <= MAX_VERTICES
        and 0 < k <= n
        and n * 20 + f * 24 + k * 24 <= MAX_GEOMETRY_BYTES
        and n * 16 + f * 12 <= MAX_GEOMETRY_BYTES
    )


def canonical(
    prepared: RenderInput, *, cache: bool = False, face_chunk_size: int = FACE_CHUNK
) -> PreparedRender:
    """Materialize canonical CPU normals; broker callers never grow cached inputs.

    Only the parser facade opts into memoization, while its native RAM permit
    still owns the source and normal arrays. Broker fallback is temporary.
    """
    import numpy as np

    if isinstance(prepared, PreparedRender):
        return prepared
    if cache and prepared._canonical is not None:
        return prepared._canonical
    result = prepare_normals(
        prepared.geometry,
        position_ids=(
            np.asarray(prepared.position_ids, dtype=np.int64)
            if isinstance(prepared, DeferredRender)
            else None
        ),
        face_chunk_size=face_chunk_size,
    )
    if cache:
        object.__setattr__(prepared, "_canonical", result)
    return result


def host_scratch_bytes(prepared: RenderInput) -> int:
    """Conservative live CSR construction and one 64k-chunk CPU fallback bound."""
    if not isinstance(prepared, DeferredRender):
        return 0
    return (
        128 * prepared.face_count
        + 16 * len(prepared.vertices)
        + 64 * prepared.position_count
        + 32 * 1024**2
    )


def device_input_bytes(prepared: DeferredRender) -> int:
    """All shared inputs, normal scratch and status; excludes objects/readback."""
    return (
        16 * len(prepared.vertices)
        + 48 * prepared.face_count
        + 16 * prepared.position_count
        + 8
    )


@dataclass(frozen=True)
class NormalPlan:
    prepared: DeferredRender
    faces: NDArray[np.uint32]
    offsets: NDArray[np.uint32]
    corners: NDArray[np.uint32]
    max_degree: int

    @property
    def n_positions(self) -> int:
        return self.prepared.position_count


def build_csr(prepared: DeferredRender) -> NormalPlan:
    """Build stable face-corner adjacency only after the owner admits scratch."""
    import numpy as np

    n, f, k = len(prepared.vertices), prepared.face_count, prepared.position_count
    if not 0 < n <= MAX_VERTICES or not 0 < f <= MAX_FACES or not 0 < k <= n:
        raise ValueError("compute_normals_shape")
    if (
        not np.isfinite(prepared.vertices).all()
        or np.max(np.abs(prepared.vertices)) > MAX_ABS_COORDINATE
    ):
        raise ValueError("compute_normals_numeric_range")
    identities = np.asarray(prepared.position_ids)
    if (
        identities.shape != (n,)
        or identities.dtype.kind not in "iu"
        or identities.min() < 0
        or identities.max() >= k
    ):
        raise ValueError("compute_normals_identity")
    faces = np.empty((f, 3), dtype="<u4")
    filled = 0
    for chunk in prepared.face_chunks(FACE_CHUNK):
        end = filled + len(chunk)
        if (
            chunk.ndim != 2
            or chunk.shape[1] != 3
            or chunk.dtype.kind not in "iu"
            or end > f
            or not len(chunk)
            or chunk.min() < 0
            or chunk.max() >= n
        ):
            raise ValueError("compute_normals_faces")
        faces[filled:end] = chunk
        filled = end
    if filled != f:
        raise ValueError("compute_normals_face_count")
    corner_positions = np.asarray(identities, dtype="<u4")[faces].ravel()
    degrees = np.bincount(corner_positions, minlength=k)
    max_degree = int(degrees.max())
    if max_degree > MAX_DEGREE:
        raise ValueError("compute_normals_valence")
    offsets = np.empty(k + 1, dtype="<u4")
    offsets[0] = 0
    np.cumsum(degrees, dtype=np.uint32, out=offsets[1:])
    corners = np.argsort(corner_positions, kind="stable").astype("<u4")
    for array in (faces, offsets, corners):
        array.flags.writeable = False
    return NormalPlan(prepared, faces, offsets, corners, max_degree)


@dataclass(frozen=True)
class DeviceNormals:
    smooth: Any
    status: Any


SHADER = """
@group(0) @binding(0) var<storage, read> positions: array<f32>;
@group(0) @binding(1) var<storage, read> indices: array<u32>;
// Six f32s per face: unit normal XYZ, followed by three interior angles.
@group(0) @binding(2) var<storage, read_write> face_data: array<f32>;
@group(0) @binding(3) var<storage, read> offsets: array<u32>;
@group(0) @binding(4) var<storage, read> corners: array<u32>;
@group(0) @binding(5) var<storage, read_write> smoothed: array<f32>;
@group(0) @binding(6) var<storage, read_write> status: atomic<u32>;

fn vertex(index: u32) -> vec3<f32> {
    let base = 3u * index;
    return vec3(positions[base], positions[base+1u], positions[base+2u]);
}
fn unit_zero(value: vec3<f32>) -> vec3<f32> {
    let size = length(value);
    if size == 0.0 { return vec3(0.0); }
    return value / size;
}
fn corner_angle(a: vec3<f32>, b: vec3<f32>) -> f32 {
    let u = a / max(length(a), 1e-20);
    let v = b / max(length(b), 1e-20);
    // Explicit additions preserve NumPy's corner dot order more closely than
    // the dot builtin (drivers may still fuse arithmetic).
    let product = u * v;
    return acos(clamp((product.x+product.y)+product.z, -1.0, 1.0));
}
fn global_index(id: vec3<u32>, groups: vec3<u32>) -> u32 {
    return id.x + id.y * groups.x * 64u;
}
@compute @workgroup_size(64)
fn faces(@builtin(global_invocation_id) id: vec3<u32>,
         @builtin(num_workgroups) groups: vec3<u32>) {
    let index = global_index(id, groups);
    if index >= arrayLength(&indices)/3u { return; }
    let a = vertex(indices[3u*index]);
    let b = vertex(indices[3u*index+1u]);
    let c = vertex(indices[3u*index+2u]);
    var normal = vec3<f32>(0.0);
    let edge1 = b-a;
    let edge2 = c-a;
    let left = edge1.yzx * edge2.zxy;
    let right = edge1.zxy * edge2.yzx;
    if all(left==right) {
        // Equality can mean a truly degenerate face or flushed subnormal
        // products. CPU accumulation can normalize the latter to a unit
        // normal, so neither case may silently become a GPU zero normal.
        atomicOr(&status, 2u);
    } else {
        let raw = left-right;
        let magnitude = max(max(abs(raw.x), abs(raw.y)), abs(raw.z));
        let products = max(abs(left), abs(right));
        let product_magnitude = max(max(products.x, products.y), products.z);
        // Decline underflow or ill-conditioned subtraction; do not clamp or
        // alter the canonical normal. This also bounds fused-product drift.
        if magnitude < 1e-18 || magnitude <= 0.001*product_magnitude {
            atomicOr(&status, 2u);
        } else {
            normal = unit_zero(raw);
        }
    }
    let base = 6u*index;
    face_data[base] = normal.x;
    face_data[base+1u] = normal.y;
    face_data[base+2u] = normal.z;
    face_data[base+3u] = corner_angle(b-a,c-a);
    face_data[base+4u] = corner_angle(c-b,a-b);
    face_data[base+5u] = corner_angle(a-c,b-c);
}
@compute @workgroup_size(64)
fn gather(@builtin(global_invocation_id) id: vec3<u32>,
          @builtin(num_workgroups) groups: vec3<u32>) {
    let index = global_index(id, groups);
    if index+1u >= arrayLength(&offsets) { return; }
    var sum = vec3<f32>(0.0);
    var correction = vec3<f32>(0.0);
    var contribution_magnitudes = 0.0;
    // Stable CSR order is original face-corner order. Neumaier summation
    // controls accumulation error without optional f64 or floating atomics.
    for (var i = offsets[index]; i < offsets[index+1u]; i += 1u) {
        let corner = corners[i];
        let base = (corner/3u)*6u;
        let normal = vec3(face_data[base], face_data[base+1u], face_data[base+2u]);
        let value = normal * face_data[base+3u+(corner%3u)];
        contribution_magnitudes += length(value);
        let next = sum + value;
        correction += select((value-next)+sum, (sum-next)+value, abs(sum)>=abs(value));
        sum = next;
    }
    let total = sum + correction;
    // This is an eligibility test, not a modified normal. The entire model
    // must be discarded and retried through canonical CPU normals if set.
    if contribution_magnitudes > 0.0 && length(total) <= 0.001*contribution_magnitudes {
        atomicOr(&status, 1u);
    }
    let normal = unit_zero(total);
    smoothed[3u*index] = normal.x;
    smoothed[3u*index+1u] = normal.y;
    smoothed[3u*index+2u] = normal.z;
}
"""


class NormalPipeline:
    def __init__(self, device: Any):
        self.device = device
        self.transfer_seconds = 0.0
        shader = device.create_shader_module(code=SHADER)
        self.faces = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "faces"}
        )
        self.gather = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "gather"}
        )

    def encode(
        self,
        plan: NormalPlan,
        encoder: Any,
        *,
        positions: Any,
        faces: Any,
        owned: list[Any],
    ) -> DeviceNormals:
        """Append allocations immediately; the caller owns every failure fence."""
        import wgpu

        if plan.max_degree > MAX_DEGREE:
            raise ValueError("compute_normals_valence")
        sizes = (
            plan.offsets.nbytes,
            plan.corners.nbytes,
            len(plan.faces) * 24,
            plan.n_positions * 12,
            4,
        )
        if max(sizes) > self.device.limits["max-storage-buffer-binding-size"]:
            raise ValueError("compute_normals_storage_budget")
        buffers = {0: positions, 1: faces}
        usage = (
            wgpu.BufferUsage.STORAGE
            | wgpu.BufferUsage.COPY_DST
            | wgpu.BufferUsage.COPY_SRC
        )
        for binding, size in zip((3, 4, 2, 5, 6), sizes, strict=True):
            buffer = self.device.create_buffer(size=size, usage=usage)
            owned.append(buffer)
            buffers[binding] = buffer
        started = time.monotonic()
        self.device.queue.write_buffer(buffers[3], 0, plan.offsets)
        self.device.queue.write_buffer(buffers[4], 0, plan.corners)
        self.transfer_seconds += time.monotonic() - started
        encoder.clear_buffer(buffers[6])
        for pipeline, bindings, count in (
            (self.faces, (0, 1, 2, 6), len(plan.faces)),
            (self.gather, (2, 3, 4, 5, 6), plan.n_positions),
        ):
            bind = self.device.create_bind_group(
                layout=pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": binding, "resource": {"buffer": buffers[binding]}}
                    for binding in bindings
                ],
            )
            compute = encoder.begin_compute_pass()
            compute.set_pipeline(pipeline)
            compute.set_bind_group(0, bind)
            groups = math.ceil(count / 64)
            x = min(groups, self.device.limits["max-compute-workgroups-per-dimension"])
            compute.dispatch_workgroups(x, math.ceil(groups / x), 1)
            compute.end()
        return DeviceNormals(buffers[5], buffers[6])

    def canary(self, *, memory: Any, fence: Any) -> None:
        """Qualify actual shader execution and arithmetic fallback, with custody."""
        import numpy as np
        import wgpu

        vertices = np.asarray(
            [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]], dtype=np.float32
        )
        tiny = np.asarray(
            [[0.0, 0.0, 0.0], [1e-20, 0.0, 0.0], [0.0, 1e-20, 0.0]],
            dtype=np.float32,
        )
        for points, opposed, expected_status in (
            (vertices, False, 0),
            (vertices, True, 1),
            (tiny, False, 2),
        ):
            points.flags.writeable = False
            faces = np.asarray(
                [[0, 1, 2], [0, 2, 1]] if opposed else [[0, 1, 2]], dtype=np.int64
            )
            faces.flags.writeable = False
            geometry = PreparedRenderGeometry(
                points, len(faces), lambda size, faces=faces: iter([faces])
            )
            prepared = defer(geometry)
            plan = build_csr(prepared)
            key = f"normal-canary:{id(self)}"
            owned: list[Any] = []
            memory.reserve(
                key,
                device_input_bytes(prepared) + 2 * prepared.position_count * 12 + 16,
                lambda owned=owned: [buffer.destroy() for buffer in owned],
                time.monotonic(),
            )
            memory.pin(key, time.monotonic())
            complete = False
            try:
                inputs = []
                for array in (prepared.vertices, plan.faces):
                    buffer = self.device.create_buffer(
                        size=array.nbytes,
                        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST,
                    )
                    owned.append(buffer)
                    self.device.queue.write_buffer(buffer, 0, array)
                    inputs.append(buffer)
                encoder = self.device.create_command_encoder()
                result = self.encode(
                    plan, encoder, positions=inputs[0], faces=inputs[1], owned=owned
                )
                self.device.queue.submit([encoder.finish()])
                values = np.frombuffer(
                    self.device.queue.read_buffer(result.smooth), dtype="<f4"
                ).reshape(-1, 3)
                status = int.from_bytes(
                    self.device.queue.read_buffer(result.status), "little"
                )
                complete = True
                expected = canonical(prepared).smooth_normals
                if (
                    status != expected_status
                    or not np.isfinite(values).all()
                    or (
                        expected_status == 0
                        and not np.allclose(values, expected, rtol=1e-5, atol=1e-6)
                    )
                ):
                    raise ValueError("compute_normals_canary")
                if expected_status == 2 and not np.any(expected):
                    raise ValueError("compute_normals_canary_fixture")
            finally:
                try:
                    if not complete:
                        fence.wait()
                finally:
                    memory.unpin(key)
                    memory.remove(key)

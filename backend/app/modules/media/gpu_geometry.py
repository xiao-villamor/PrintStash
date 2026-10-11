"""Portable indexed geometry packing with explicit transient buffer ownership.

Only validated, already centered render arrays enter this owner. Source parsing,
source-precision measurements remain CPU work. Guarded smooth-normal preparation
and indexed crease-normal packing share GPU inputs and their completion fence.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from printstash_core.mesh.render_geometry import PreparedRender

from app.runtime.compute.budget import Residency
from app.runtime.compute.contracts import ComputeUnavailable, Reason

from .gpu_normals import (
    DeferredRender,
    NormalPipeline,
    NormalPlan,
    RenderInput,
    build_csr,
    canonical,
)

FACE_CHUNK = 64_000
# Completed warm upload/render measurements on indexed meshes and STL soup
# crossed 1.5x at ~20k faces; small inputs keep canonical CPU packing. Adapter
# qualification still controls render admission and validates the startup canary.
GPU_PACK_MIN_FACES: int | None = 20_000

SHADER = """
struct Triangle { a: vec4<f32>, b: vec4<f32>, c: vec4<f32>,
                  na: vec4<f32>, nb: vec4<f32>, nc: vec4<f32> };
@group(0) @binding(0) var<storage, read> positions: array<f32>;
@group(0) @binding(1) var<storage, read> faces: array<u32>;
@group(0) @binding(2) var<storage, read> identities: array<u32>;
@group(0) @binding(3) var<storage, read> smoothed: array<f32>;
@group(0) @binding(4) var<storage, read_write> objects: array<Triangle>;
fn point(index: u32) -> vec3<f32> {
    let p = index * 3u;
    return vec3(positions[p], positions[p+1u], positions[p+2u]);
}
fn normal(index: u32) -> vec3<f32> {
    let p = identities[index] * 3u;
    return vec3(smoothed[p], smoothed[p+1u], smoothed[p+2u]);
}
fn unit(value: vec3<f32>) -> vec3<f32> {
    let magnitude = length(value);
    return value / select(magnitude, 1.0, magnitude == 0.0);
}
fn blend(index: u32, flat: vec3<f32>) -> vec4<f32> {
    let interpolated = normal(index);
    var t = clamp((dot(interpolated, flat)-0.75)/0.17, 0.0, 1.0);
    t = t*t*(3.0-2.0*t);
    return vec4(unit(t*interpolated+(1.0-t)*flat), 0.0);
}
@compute @workgroup_size(64)
fn pack(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x >= arrayLength(&objects) { return; }
    let f = id.x * 3u;
    let ia = faces[f]; let ib = faces[f+1u]; let ic = faces[f+2u];
    let a = point(ia); let b = point(ib); let c = point(ic);
    let u = b-a; let v = c-a;
    let positive = u.yzx * v.zxy;
    let negative = u.zxy * v.yzx;
    var flat = vec3(0.0);
    // A fused cross(v,v) may leave a tiny residue on some drivers. Comparing
    // rounded products preserves the CPU's exact-zero rule before normalizing
    // repeated or collinear positions; no arbitrary area cutoff is introduced.
    if !all(a==b) && !all(b==c) && !all(c==a) && !all(positive==negative) {
        flat = unit(positive-negative);
    }
    objects[id.x] = Triangle(vec4(a,0.0), vec4(b,0.0), vec4(c,0.0),
                            blend(ia,flat), blend(ib,flat), blend(ic,flat));
}
"""


def input_sizes(prepared: RenderInput) -> tuple[int, ...]:
    """Exact owned device bytes, including deferred-normal scratch and status."""
    if isinstance(prepared, DeferredRender):
        return (
            len(prepared.vertices) * 12,
            prepared.face_count * 12,
            len(prepared.position_ids) * 4,
            (prepared.position_count + 1) * 4,
            prepared.face_count * 12,
            prepared.face_count * 24,
            prepared.position_count * 12,
            4,
        )
    return (
        len(prepared.vertices) * 12,
        prepared.face_count * 12,
        len(prepared.position_ids) * 4,
        len(prepared.smooth_normals) * 12,
    )


def packed_chunks(prepared: PreparedRender) -> Iterator[NDArray[np.float32]]:
    """Canonical CPU packing retained for small work and unavailable GPU setup."""
    for faces in prepared.face_chunks(FACE_CHUNK):
        positions = prepared.vertices[faces]
        flat = np.cross(
            positions[:, 1] - positions[:, 0], positions[:, 2] - positions[:, 0]
        )
        lengths = np.linalg.norm(flat, axis=1, keepdims=True)
        flat /= np.where(lengths == 0, 1.0, lengths)
        smooth = prepared.smooth_normals[prepared.position_ids[faces]]
        t = np.clip((np.sum(smooth * flat[:, None, :], axis=2) - 0.75) / 0.17, 0.0, 1.0)
        t = (t * t * (3.0 - 2.0 * t))[..., None]
        normals = t * smooth + (1.0 - t) * flat[:, None, :]
        lengths = np.linalg.norm(normals, axis=2, keepdims=True)
        normals /= np.where(lengths == 0, 1.0, lengths)
        values = np.zeros((len(faces), 6, 4), dtype="<f4")
        values[:, :3, :3] = positions
        values[:, 3:, :3] = normals
        yield values


@dataclass
class _Pending:
    key: str
    buffers: list[Any]
    geometry_key: str
    status: Any = None


class CompletionFence:
    """Preallocated queue completion without a callback or allocation on failure."""

    def __init__(self, device: Any, memory: Residency):
        import wgpu

        self.device = device
        self.memory = memory
        self.key = f"packing-fence:{id(self)}"
        self.buffer = None
        self.closed = False

        def release() -> None:
            if self.buffer is not None:
                self.buffer.destroy()
                self.buffer = None
            self.closed = True

        memory.reserve(self.key, 4, release, time.monotonic())
        memory.pin(self.key, time.monotonic())
        try:
            self.buffer = device.create_buffer(
                size=4, usage=wgpu.BufferUsage.MAP_READ | wgpu.BufferUsage.COPY_DST
            )
        except BaseException:
            self.close()
            raise

    def wait(self) -> None:
        import wgpu

        if self.closed:
            raise RuntimeError("compute_geometry_fence_closed")
        assert self.buffer is not None
        encoder = self.device.create_command_encoder()
        encoder.clear_buffer(self.buffer)
        self.device.queue.submit([encoder.finish()])
        self.buffer.map_sync(wgpu.MapMode.READ)
        self.buffer.unmap()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.memory.unpin(self.key)
            self.memory.remove(self.key)


class GeometryPacker:
    def __init__(self, device: Any, memory: Residency):
        self.device = device
        self.memory = memory
        self.pending: list[_Pending] = []
        shader = device.create_shader_module(code=SHADER)
        self.pipeline = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "pack"}
        )
        self.fence = CompletionFence(device, memory)
        self.submissions = 0
        self.transferred_bytes = 0
        self.transfer_seconds = 0.0
        self.normals: NormalPipeline | None = None
        self.normals_attempted = False
        self.gpu_normal_uploads = 0
        self.cpu_normal_fallbacks = 0
        self.normal_seconds = 0.0

    def _normal_plan(
        self, prepared: RenderInput
    ) -> tuple[RenderInput, NormalPlan | None]:
        if not isinstance(prepared, DeferredRender):
            return prepared, None
        started = time.monotonic()
        try:
            if not self.normals_attempted:
                self.normals_attempted = True
                try:
                    candidate = NormalPipeline(self.device)
                    candidate.canary(memory=self.memory, fence=self.fence)
                    self.normals = candidate
                except Exception as exc:
                    from app.core.logging import get_logger

                    get_logger(__name__).warning(
                        "GPU normal preparation unavailable: %s", exc
                    )
            if self.normals is not None:
                try:
                    return prepared, build_csr(prepared)
                except ValueError as exc:
                    if str(exc) not in {
                        "compute_normals_valence",
                        "compute_normals_numeric_range",
                    }:
                        raise
            self.cpu_normal_fallbacks += 1
            return canonical(prepared), None
        finally:
            self.normal_seconds += time.monotonic() - started

    def normal_statuses(self) -> dict[str, Any]:
        return {
            item.geometry_key: item.status
            for item in self.pending
            if item.status is not None
        }

    def submit(self, prepared: RenderInput, target: Any, key: str) -> None:
        """Queue normals and packing; every input lease survives the frame fence."""
        import wgpu

        prepared, plan = self._normal_plan(prepared)
        sizes = input_sizes(prepared)
        limit = self.device.limits["max-storage-buffer-binding-size"]
        if max(sizes) > limit:
            raise ComputeUnavailable(Reason.CAPACITY)
        pending = _Pending("packing:" + key, [], key)

        def release() -> None:
            for buffer in pending.buffers:
                buffer.destroy()

        self.memory.reserve(pending.key, sum(sizes), release, time.monotonic())
        self.memory.pin(pending.key, time.monotonic())
        try:
            for size in sizes[:3] if plan is not None else sizes:
                pending.buffers.append(
                    self.device.create_buffer(
                        size=size,
                        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST,
                    )
                )
            started = time.monotonic()
            self.device.queue.write_buffer(pending.buffers[0], 0, prepared.vertices)
            if plan is not None:
                self.device.queue.write_buffer(pending.buffers[1], 0, plan.faces)
            else:
                offset = 0
                for faces in prepared.face_chunks(FACE_CHUNK):
                    indices = np.asarray(faces, dtype="<u4")
                    self.device.queue.write_buffer(pending.buffers[1], offset, indices)
                    offset += indices.nbytes
            self.device.queue.write_buffer(
                pending.buffers[2], 0, np.asarray(prepared.position_ids, dtype="<u4")
            )
            self.transfer_seconds += time.monotonic() - started
            encoder = self.device.create_command_encoder()
            if plan is not None:
                assert self.normals is not None
                started = time.monotonic()
                transfer_seconds = self.normals.transfer_seconds
                generated = self.normals.encode(
                    plan,
                    encoder,
                    positions=pending.buffers[0],
                    faces=pending.buffers[1],
                    owned=pending.buffers,
                )
                self.normal_seconds += time.monotonic() - started
                self.transfer_seconds += (
                    self.normals.transfer_seconds - transfer_seconds
                )
                smooth = generated.smooth
                pending.status = generated.status
                transferred = sum(sizes[:3]) + plan.offsets.nbytes + plan.corners.nbytes
                self.gpu_normal_uploads += 1
            else:
                assert isinstance(prepared, PreparedRender)
                smooth = pending.buffers[3]
                started = time.monotonic()
                self.device.queue.write_buffer(
                    smooth, 0, np.asarray(prepared.smooth_normals, dtype="<f4")
                )
                self.transfer_seconds += time.monotonic() - started
                transferred = sum(sizes)
            group = self.device.create_bind_group(
                layout=self.pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": index, "resource": {"buffer": buffer}}
                    for index, buffer in enumerate(
                        [*pending.buffers[:3], smooth, target]
                    )
                ],
            )
            compute = encoder.begin_compute_pass()
            compute.set_pipeline(self.pipeline)
            compute.set_bind_group(0, group)
            compute.dispatch_workgroups((prepared.face_count + 63) // 64)
            compute.end()
            # Native submission failures may leave earlier queue writes pending.
            self.pending.append(pending)
            self.device.queue.submit([encoder.finish()])
            self.submissions += 1
            self.transferred_bytes += transferred
        except BaseException:
            if pending not in self.pending:
                self.pending.append(pending)
            raise

    def release_completed(self, *, fence_complete: bool) -> None:
        """Release together after the frame map, or one exceptional cleanup fence."""
        if not self.pending:
            return
        try:
            if not fence_complete:
                self.fence.wait()
        finally:
            pending, self.pending = self.pending, []
            for item in pending:
                self.memory.unpin(item.key)
                self.memory.remove(item.key)

    def close(self) -> None:
        try:
            self.release_completed(fence_complete=False)
        finally:
            self.fence.close()

    def canary(self) -> None:
        """Execute real packing before admitting it; retain no canary allocations."""
        import wgpu

        random = np.random.default_rng(29)
        random_vertices = random.normal(size=(100, 3)).astype(np.float32)
        random_faces = random.integers(0, 100, size=(500, 3), dtype=np.int64)
        random_normals = random.normal(size=(100, 3))
        random_normals /= np.linalg.norm(random_normals, axis=1, keepdims=True)
        vertices = np.concatenate(
            (
                np.asarray(
                    [
                        [0, 0, 0],
                        [10, 0, 0],
                        [0, 10, 0],
                        [0, 0, 0],
                        [0.1234567, 0.4455667, 0.9876543],
                        [0.2469134, 0.8911334, 1.9753086],
                    ],
                    dtype=np.float32,
                ),
                random_vertices,
            )
        )
        faces = np.concatenate(
            (
                np.asarray(
                    [[0, 1, 2], [0, 0, 0], [3, 2, 1], [0, 4, 5]], dtype=np.int64
                ),
                random_faces + 6,
            )
        )
        prepared = PreparedRender(
            vertices,
            len(faces),
            lambda size: iter([faces]),
            np.concatenate(
                (
                    np.asarray([2, 0, 1, 2, 0, 0], dtype=np.int64),
                    np.arange(3, 103, dtype=np.int64),
                )
            ),
            np.concatenate(
                (
                    np.asarray(
                        [
                            [np.sqrt(1 - 0.8**2), 0, 0.8],
                            [0, np.sqrt(1 - 0.75**2), 0.75],
                            [np.sqrt(1 - 0.92**2), 0, 0.92],
                        ]
                    ),
                    random_normals,
                )
            ),
        )
        output_bytes = prepared.face_count * 96
        key = "packing-canary-output"
        output = None

        def release() -> None:
            if output is not None:
                output.destroy()

        self.memory.reserve(key, output_bytes * 2, release, time.monotonic())
        self.memory.pin(key, time.monotonic())
        complete = False
        try:
            output = self.device.create_buffer(
                size=output_bytes,
                usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
            )
            self.submit(prepared, output, key)
            actual = np.frombuffer(self.device.queue.read_buffer(output), dtype="<f4")
            complete = True
            expected = np.concatenate(tuple(packed_chunks(prepared))).reshape(-1)
            if not np.isfinite(actual).all() or not np.allclose(
                actual, expected, rtol=1e-5, atol=1e-6
            ):
                raise ValueError("compute_geometry_packing_canary")
        finally:
            try:
                self.release_completed(fence_complete=complete)
            finally:
                self.memory.unpin(key)
                self.memory.remove(key)

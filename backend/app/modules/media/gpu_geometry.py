"""Portable indexed geometry packing with explicit transient buffer ownership.

Only validated, already centered render arrays enter this owner. Source parsing
and source-precision measurements remain CPU work. Indexed packing consumes
the renderer's shared face-corner normals.
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

from .gpu_normals import RenderInput, canonical

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
@group(0) @binding(2) var<storage, read> corner_normals: array<f32>;
@group(0) @binding(3) var<storage, read_write> objects: array<Triangle>;
fn point(index: u32) -> vec4<f32> {
    let p = index * 3u;
    return vec4(positions[p], positions[p+1u], positions[p+2u], 0.0);
}
fn normal(index: u32) -> vec4<f32> {
    let p = index * 3u;
    return vec4(corner_normals[p], corner_normals[p+1u], corner_normals[p+2u], 0.0);
}
@compute @workgroup_size(64)
fn pack(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x >= arrayLength(&objects) { return; }
    let f = id.x * 3u;
    objects[id.x] = Triangle(point(faces[f]), point(faces[f+1u]), point(faces[f+2u]),
                            normal(f), normal(f+1u), normal(f+2u));
}
"""


def input_sizes(prepared: RenderInput) -> tuple[int, ...]:
    return (
        len(prepared.vertices) * 12,
        prepared.face_count * 12,
        prepared.face_count * 36,
    )


def packed_chunks(prepared: PreparedRender) -> Iterator[NDArray[np.float32]]:
    """Canonical CPU packing retained for small work and unavailable GPU setup."""
    start = 0
    for faces in prepared.face_chunks(FACE_CHUNK):
        end = start + len(faces)
        values = np.zeros((len(faces), 6, 4), dtype="<f4")
        values[:, :3, :3] = prepared.vertices[faces]
        values[:, 3:, :3] = prepared.corner_normals[start:end]
        yield values
        start = end


@dataclass
class _Pending:
    key: str
    buffers: list[Any]


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

    def submit(self, prepared: RenderInput, target: Any, key: str) -> None:
        """Queue normals and packing; every input lease survives the frame fence."""
        import wgpu

        prepared = canonical(prepared)
        sizes = input_sizes(prepared)
        limit = self.device.limits["max-storage-buffer-binding-size"]
        if max(sizes) > limit:
            raise ComputeUnavailable(Reason.CAPACITY)
        pending = _Pending("packing:" + key, [])

        def release() -> None:
            for buffer in pending.buffers:
                buffer.destroy()

        self.memory.reserve(pending.key, sum(sizes), release, time.monotonic())
        self.memory.pin(pending.key, time.monotonic())
        try:
            for size in sizes:
                pending.buffers.append(
                    self.device.create_buffer(
                        size=size,
                        usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST,
                    )
                )
            started = time.monotonic()
            self.device.queue.write_buffer(pending.buffers[0], 0, prepared.vertices)
            offset = 0
            for faces in prepared.face_chunks(FACE_CHUNK):
                indices = np.asarray(faces, dtype="<u4")
                self.device.queue.write_buffer(pending.buffers[1], offset, indices)
                offset += indices.nbytes
            self.device.queue.write_buffer(
                pending.buffers[2], 0, prepared.corner_normals
            )
            self.transfer_seconds += time.monotonic() - started
            encoder = self.device.create_command_encoder()
            group = self.device.create_bind_group(
                layout=self.pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": index, "resource": {"buffer": buffer}}
                    for index, buffer in enumerate([*pending.buffers, target])
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
            self.transferred_bytes += sum(sizes)
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
        from printstash_core.mesh.render_geometry import (
            PreparedRenderGeometry,
            prepare_normals,
        )

        prepared = prepare_normals(
            PreparedRenderGeometry(vertices, len(faces), lambda size: iter([faces]))
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

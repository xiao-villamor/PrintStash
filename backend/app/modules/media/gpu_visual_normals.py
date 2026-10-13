"""Portable visual normals without CPU welding, sorting or per-model readbacks.

Exact float32 positions weld through a bounded device hash table. Angle-weighted
normal sums use core integer atomics with a fixed visual precision (2**16).
Overflow, hash congestion and degenerate geometry degrade locally to flat normals;
they never discard healthy triangles or force a whole model to be rendered twice.
Authoritative geometry measurements do not use these visual approximations.
"""

from __future__ import annotations

import math
from typing import Any

from .gpu_normals import DeviceNormals, VisualRender


def visual_scratch_sizes(vertices: int) -> tuple[int, int, int, int]:
    if type(vertices) is not int or vertices < 1:
        raise ValueError("compute_normals_shape")
    capacity = 1 << (2 * vertices - 1).bit_length()
    return capacity * 4, capacity * 12, capacity * 4, 4


SHADER = """
@group(0) @binding(0) var<storage, read> positions: array<f32>;
@group(0) @binding(1) var<storage, read> faces: array<u32>;
@group(0) @binding(2) var<storage, read_write> identities: array<u32>;
@group(0) @binding(3) var<storage, read_write> smoothed: array<f32>;
@group(0) @binding(4) var<storage, read_write> heads: array<atomic<u32>>;
@group(0) @binding(5) var<storage, read_write> sums: array<atomic<i32>>;
@group(0) @binding(6) var<storage, read_write> degrees: array<atomic<u32>>;
@group(0) @binding(7) var<storage, read_write> fallback_vertices: atomic<u32>;

fn vertex(i: u32) -> vec3<f32> {
    return vec3(positions[3u*i], positions[3u*i+1u], positions[3u*i+2u]);
}
fn global_index(id: vec3<u32>, groups: vec3<u32>) -> u32 {
    return id.x + id.y * groups.x * 64u;
}
fn bits(p: vec3<f32>) -> vec3<u32> {
    return select(bitcast<vec3<u32>>(p), vec3(0u), p == vec3(0.0));
}
fn mix_hash(value: u32) -> u32 {
    var x = value;
    x = (x ^ (x >> 16u)) * 0x7feb352du;
    x = (x ^ (x >> 15u)) * 0x846ca68bu;
    return x ^ (x >> 16u);
}
fn unit_zero(p: vec3<f32>) -> vec3<f32> {
    let scale = max(max(abs(p.x), abs(p.y)), abs(p.z));
    if scale == 0.0 { return vec3(0.0); }
    let scaled = p / scale;
    return scaled / length(scaled);
}

@compute @workgroup_size(64)
fn weld(@builtin(global_invocation_id) id: vec3<u32>, @builtin(num_workgroups) groups: vec3<u32>) {
    let i = global_index(id, groups);
    if i >= arrayLength(&identities) { return; }
    let key = bits(vertex(i));
    let mask = arrayLength(&heads) - 1u;
    var slot = mix_hash(key.x ^ mix_hash(key.y ^ mix_hash(key.z))) & mask;
    identities[i] = 0xffffffffu;
    for (var probe = 0u; probe < 64u; probe++) {
        let claimed = atomicCompareExchangeWeak(&heads[slot], 0u, i+1u);
        if claimed.exchanged {
            identities[i] = slot;
            return;
        }
        if claimed.old_value == 0u { continue; }
        if all(bits(vertex(claimed.old_value-1u)) == key) {
            identities[i] = slot;
            return;
        }
        slot = (slot + 1u) & mask;
    }
}

@compute @workgroup_size(64)
fn accumulate(@builtin(global_invocation_id) id: vec3<u32>, @builtin(num_workgroups) groups: vec3<u32>) {
    let f = global_index(id, groups);
    if f >= arrayLength(&faces)/3u { return; }
    let indices = vec3(faces[3u*f], faces[3u*f+1u], faces[3u*f+2u]);
    let a = vertex(indices.x);
    let b = vertex(indices.y);
    let c = vertex(indices.z);
    let ab = unit_zero(b-a);
    let ac = unit_zero(c-a);
    let bc = unit_zero(c-b);
    let normal = unit_zero(cross(ab, ac));
    if all(normal == vec3(0.0)) { return; }
    let weights = vec3(acos(clamp(dot(ab, ac), -1.0, 1.0)),
                       acos(clamp(dot(-ab, bc), -1.0, 1.0)),
                       acos(clamp(dot(-ac, -bc), -1.0, 1.0)));
    for (var corner = 0u; corner < 3u; corner++) {
        let slot = identities[indices[corner]];
        if slot == 0xffffffffu { continue; }
        // At most 4096*pi*65536 per component, below signed32-bit overflow.
        let degree = atomicAdd(&degrees[slot], 1u);
        if degree >= 4096u { continue; }
        let value = vec3<i32>(round(normal * weights[corner] * 65536.0));
        atomicAdd(&sums[3u*slot], value.x);
        atomicAdd(&sums[3u*slot+1u], value.y);
        atomicAdd(&sums[3u*slot+2u], value.z);
    }
}

@compute @workgroup_size(64)
fn normalize(@builtin(global_invocation_id) id: vec3<u32>, @builtin(num_workgroups) groups: vec3<u32>) {
    let i = global_index(id, groups);
    if i >= arrayLength(&identities) { return; }
    let slot = identities[i];
    var normal = vec3(0.0);
    if slot != 0xffffffffu {
        if atomicLoad(&degrees[slot]) <= 4096u {
            normal = unit_zero(vec3<f32>(f32(atomicLoad(&sums[3u*slot])),
                       f32(atomicLoad(&sums[3u*slot+1u])), f32(atomicLoad(&sums[3u*slot+2u]))));
        }
    }
    if all(normal == vec3(0.0)) { atomicAdd(&fallback_vertices, 1u); }
    smoothed[3u*i] = normal.x;
    smoothed[3u*i+1u] = normal.y;
    smoothed[3u*i+2u] = normal.z;
    identities[i] = i;
}
"""


class VisualNormalPipeline:
    def __init__(self, device: Any):
        self.device = device
        shader = device.create_shader_module(code=SHADER)
        self.passes = tuple(
            device.create_compute_pipeline(
                layout="auto", compute={"module": shader, "entry_point": entry}
            )
            for entry in ("weld", "accumulate", "normalize")
        )

    def encode(
        self,
        prepared: VisualRender,
        encoder: Any,
        *,
        positions: Any,
        faces: Any,
        identities: Any,
        smoothed: Any,
        owned: list[Any],
    ) -> DeviceNormals:
        """Record three passes into the caller's one batch submission."""
        import wgpu

        sizes = visual_scratch_sizes(len(prepared.vertices))
        if max(sizes) > self.device.limits["max-storage-buffer-binding-size"]:
            raise ValueError("compute_normals_storage_budget")
        buffers = {0: positions, 1: faces, 2: identities, 3: smoothed}
        for binding, size in zip((4, 5, 6, 7), sizes, strict=True):
            buffer = self.device.create_buffer(
                size=size,
                usage=wgpu.BufferUsage.STORAGE
                | wgpu.BufferUsage.COPY_SRC
                | wgpu.BufferUsage.COPY_DST,
            )
            owned.append(buffer)
            buffers[binding] = buffer
            encoder.clear_buffer(buffer)
        for pipeline, bindings, count in zip(
            self.passes,
            ((0, 2, 4), (0, 1, 2, 5, 6), (2, 3, 5, 6, 7)),
            (len(prepared.vertices), prepared.face_count, len(prepared.vertices)),
            strict=True,
        ):
            bind = self.device.create_bind_group(
                layout=pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": i, "resource": {"buffer": buffers[i]}} for i in bindings
                ],
            )
            compute = encoder.begin_compute_pass()
            compute.set_pipeline(pipeline)
            compute.set_bind_group(0, bind)
            groups = math.ceil(count / 64)
            x = min(groups, self.device.limits["max-compute-workgroups-per-dimension"])
            compute.dispatch_workgroups(x, math.ceil(groups / x), 1)
            compute.end()
        return DeviceNormals(smoothed, buffers[7])

    def canary(self, *, memory: Any, fence: Any) -> None:
        """Exercise welding and local degeneracy once per persistent pipeline."""
        import time

        import numpy as np
        import wgpu
        from printstash_core.mesh.render_geometry import PreparedRenderGeometry

        # Two copies share exact positions; the last face is degenerate.
        vertices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]] * 2, dtype="<f4")
        indices = np.asarray([[0, 1, 2], [3, 4, 5], [0, 0, 0]], dtype="<u4")
        prepared = VisualRender(
            PreparedRenderGeometry(vertices, 3, lambda size: iter([indices]))
        )
        owned: list[Any] = []
        key = f"visual-normal-canary:{id(self)}"
        total = (
            vertices.nbytes
            + indices.nbytes
            + len(vertices) * 16
            + sum(visual_scratch_sizes(len(vertices)))
        )
        memory.reserve(
            key, total, lambda: [buffer.destroy() for buffer in owned], time.monotonic()
        )
        memory.pin(key, time.monotonic())
        complete = False
        try:
            inputs = []
            for array in (vertices, indices):
                buffer = self.device.create_buffer(
                    size=array.nbytes,
                    usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST,
                )
                owned.append(buffer)
                self.device.queue.write_buffer(buffer, 0, array)
                inputs.append(buffer)
            outputs = []
            for size in (len(vertices) * 4, len(vertices) * 12):
                buffer = self.device.create_buffer(
                    size=size,
                    usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
                )
                owned.append(buffer)
                outputs.append(buffer)
            encoder = self.device.create_command_encoder()
            result = self.encode(
                prepared,
                encoder,
                positions=inputs[0],
                faces=inputs[1],
                identities=outputs[0],
                smoothed=outputs[1],
                owned=owned,
            )
            self.device.queue.submit([encoder.finish()])
            normals = np.frombuffer(
                self.device.queue.read_buffer(result.smooth), dtype="<f4"
            ).reshape(-1, 3)
            status = int.from_bytes(
                self.device.queue.read_buffer(result.status), "little"
            )
            complete = True
            if status != 0 or not np.allclose(normals, [0, 0, 1], atol=1e-5):
                raise ValueError("compute_visual_normals_canary")
        finally:
            try:
                if not complete:
                    fence.wait()
            finally:
                memory.unpin(key)
                memory.remove(key)

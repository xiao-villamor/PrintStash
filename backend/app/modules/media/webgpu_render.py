"""Persistent portable geometry, projection, culling, shading and rasterization.

Core owns framing, silhouette policy and final CPU image processing. Only bounded
validated arrays enter this owner. Qualification must pass on the exact runtime,
recipe and physical adapter before the broker can select it.
"""

import hashlib
import struct
import time
from collections import OrderedDict

import numpy as np
from printstash_core.mesh import rasterizer as core
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE, RASTERIZER_RECIPE

from app.runtime.compute.budget import Residency

from .compute_geometry import decode, identity, layout_for
from .gpu_geometry import GPU_PACK_MIN_FACES, GeometryPacker, packed_chunks
from .gpu_normals import DeferredRender, canonical

OUTPUT_CACHE_BYTES = 32 * 1024**2

SHADER = """
struct Triangle { a: vec4<f32>, b: vec4<f32>, c: vec4<f32>,
                  na: vec4<f32>, nb: vec4<f32>, nc: vec4<f32> };
struct Frame {
    r0: vec4<f32>, r1: vec4<f32>, r2: vec4<f32>,
    projection: vec4<f32>, extent: vec4<f32>, style: vec4<f32>,
    albedo: vec4<f32>
};
@group(0) @binding(0) var<storage, read> objects: array<Triangle>;
@group(0) @binding(1) var<storage, read_write> projected: array<Triangle>;
@group(0) @binding(2) var<storage, read_write> visible: atomic<u32>;
@group(0) @binding(3) var<uniform> frame: Frame;

fn rotate(p: vec3<f32>) -> vec3<f32> {
    return vec3(dot(p, frame.r0.xyz), dot(p, frame.r1.xyz), dot(p, frame.r2.xyz));
}
fn project(p: vec3<f32>) -> vec3<f32> {
    return vec3((p.x-frame.projection.x)*frame.projection.z+frame.extent.x*0.5,
                frame.extent.y*0.5-(p.y-frame.projection.y)*frame.projection.z, p.z);
}
fn normal(n: vec3<f32>) -> vec4<f32> {
    let v = rotate(n);
    return vec4(select(-v, v, v.z >= 0.0), 0.);
}
@compute @workgroup_size(64)
fn prepare(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x >= arrayLength(&objects) { return; }
    let t = objects[id.x];
    let a = rotate(t.a.xyz); let b = rotate(t.b.xyz); let c = rotate(t.c.xyz);
    let n = cross(b-a,c-a);
    let front = n.z * frame.projection.w < 0.0 && length(n) > 1e-8;
    if front { atomicAdd(&visible, 1u); }
    var result: Triangle;
    result.a = vec4(project(a), select(0., 1., front));
    result.b = vec4(project(b), 0.);
    result.c = vec4(project(c), 0.);
    result.na = normal(t.na.xyz); result.nb = normal(t.nb.xyz); result.nc = normal(t.nc.xyz);
    projected[id.x] = result;
}

struct Vertex { @builtin(position) pos: vec4<f32>,
                @location(0) @interpolate(flat) triangle: u32 };
@vertex fn vs(@builtin(vertex_index) vertex: u32, @builtin(instance_index) index: u32) -> Vertex {
    let t = projected[index];
    var out: Vertex;
    out.triangle = index;
    if atomicLoad(&visible) > 0u && t.a.w == 0.0 {
        out.pos = vec4(2., 2., 0., 1.);
        return out;
    }
    let low = floor(min(t.a.xy, min(t.b.xy, t.c.xy)));
    let high = ceil(max(t.a.xy, max(t.b.xy, t.c.xy))) + vec2<f32>(1.0);
    let corners = array<vec2<f32>, 6>(vec2(0.,0.), vec2(1.,0.), vec2(0.,1.),
                                    vec2(0.,1.), vec2(1.,0.), vec2(1.,1.));
    let pixel = mix(low, high, corners[vertex]);
    out.pos = vec4(2.0 * pixel.x / frame.extent.x - 1.0, 1.0 - 2.0 * pixel.y / frame.extent.y, 0.0, 1.0);
    return out;
}
fn shade(value: vec3<f32>) -> vec3<f32> {
    if atomicLoad(&visible) == 0u { return frame.albedo.xyz * 0.6; }
    let n = value / max(length(value), 1e-30);
    let key = normalize(vec3(-0.5, 0.65, 1.));
    let fill = normalize(vec3(0.55, -0.25, 0.55));
    let half_vector = normalize(key+vec3(0., 0., 1.));
    let diffuse = (0.3 + 1.05*clamp(dot(n,key),0.,1.)*vec3(1.,0.98,0.95)
                       + 0.3*clamp(dot(n,fill),0.,1.)*vec3(0.55,0.62,0.78)) * frame.albedo.xyz;
    let fresnel = pow(1.-clamp(n.z,0.,1.), 3.);
    let specular = frame.style.x * pow(clamp(dot(n,half_vector),0.,1.),32.);
    return clamp(diffuse+0.22*fresnel*vec3(0.85,0.92,1.)+specular,vec3(0.),vec3(1.));
}
// Shared edges use one arithmetic order in both incident triangles. This
// preserves opposite signs even when the driver contracts multiply/add.
fn edge(start: vec2<f32>, end: vec2<f32>, p: vec2<f32>) -> f32 {
    let reverse = start.x > end.x || (start.x == end.x && start.y > end.y);
    let low = select(start, end, reverse);
    let high = select(end, start, reverse);
    let value = (high.y-low.y)*(p.x-low.x)+(low.x-high.x)*(p.y-low.y);
    return select(value, -value, reverse);
}
struct Fragment { @location(0) color: vec4<f32>, @builtin(frag_depth) depth: f32 };
@fragment fn fs(in: Vertex) -> Fragment {
    let t = projected[in.triangle];
    let p = in.pos.xy;
    let d = (t.b.y-t.c.y)*(t.a.x-t.c.x)+(t.c.x-t.b.x)*(t.a.y-t.c.y);
    if abs(d) <= 1e-9 { discard; }
    let a = edge(t.c.xy,t.b.xy,p)/d;
    let b = edge(t.a.xy,t.c.xy,p)/d;
    // Evaluate the third edge directly. Subtracting the first two weights
    // loses opacity along shared edges through single-precision cancellation.
    let c = edge(t.b.xy,t.a.xy,p)/d;
    if a < 0.0 || b < 0.0 || c < 0.0 { discard; }
    let z = a*t.a.z+b*t.b.z+c*t.c.z;
    var out: Fragment;
    out.depth = clamp((z-frame.extent.z)/frame.extent.w,0.,1.)*0.999999;
    out.color = vec4(floor(255.*shade(a*t.na.xyz+b*t.nb.xyz+c*t.nc.xyz))/255.,1.);
    return out;
}
"""


class Renderer:
    def __init__(
        self,
        device,
        memory: Residency | None = None,
        *,
        packing_min_faces: int | None = GPU_PACK_MIN_FACES,
    ):
        self.device = device
        self.memory = memory if memory is not None else Residency(1024**3)
        self.geometry = {}
        self.unverified_normals: set[str] = set()
        self.transfers = self.cache_hits = self.uploads = 0
        self.output_hits = self.submissions = self.readback_batches = 0
        self.batch_model_counts = [0] * 8
        self.batch_frame_counts = [0] * 8
        self.outputs = OrderedDict()
        self.output_bytes = 0
        self.readback_frames = 1
        self.frame_offset = 0
        self.transfer_seconds = 0.0
        self.packing_min_faces = packing_min_faces
        self.packer = None
        self.packing_attempted = False
        self.gpu_geometry_uploads = self.cpu_geometry_uploads = 0
        self.geometry_packing_seconds = 0.0
        # GPU normal submissions include numerical refusals; fallback count
        # reports the subset retried with canonical CPU preparation.
        self.geometry_gpu_normal_uploads = self.geometry_cpu_normal_fallbacks = 0
        self.geometry_normal_seconds = 0.0
        compute_source, vertex_source = SHADER.split("struct Vertex", 1)
        compute_shader = device.create_shader_module(code=compute_source)
        render_source = (
            compute_source.split("@group(0)")[0]
            + """
@group(0) @binding(1) var<storage, read> projected: array<Triangle>;
@group(0) @binding(2) var<storage, read> visible: u32;
@group(0) @binding(3) var<uniform> frame: Frame;
"""
            + "struct Vertex"
            + vertex_source.replace("atomicLoad(&visible)", "visible")
        )
        shader = device.create_shader_module(code=render_source)
        self.compute = device.create_compute_pipeline(
            layout="auto", compute={"module": compute_shader, "entry_point": "prepare"}
        )
        self.pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": shader, "entry_point": "vs", "buffers": []},
            primitive={"topology": "triangle-list", "cull_mode": "none"},
            depth_stencil={
                "format": "depth32float",
                "depth_write_enabled": True,
                "depth_compare": "less",
            },
            multisample={"count": 1},
            fragment={
                "module": shader,
                "entry_point": "fs",
                "targets": [{"format": "rgba8unorm"}],
            },
        )
        self.color = self.depth = self.projected = self.uniform = self.visible = None
        self.readback = None
        self.readback_size = 0
        self.size = None
        self.projected_size = 0
        self.workspace_limit = self.memory.capacity
        self.postprocessor = None
        self.postprocess_attempted = False
        self.postprocess_size = 0
        self.gpu_finalize_active = False
        self.readback_frame_bytes = None
        self.readback_status_bytes = 0

    def upload(self, prepared, key):
        import wgpu

        if key in self.geometry:
            self.cache_hits += 1
            return self.geometry[key]
        size = prepared.face_count * 96
        if size > self.device.limits["max-storage-buffer-binding-size"]:
            from app.runtime.compute.contracts import ComputeUnavailable, Reason

            raise ComputeUnavailable(Reason.CAPACITY)
        self.memory.make_room(size)

        buffer = None

        def release():
            if buffer is not None:
                self.geometry.pop(key).destroy()

        self.memory.reserve(key, size, release, time.monotonic())
        self.memory.pin(key, time.monotonic())
        try:
            buffer = self.device.create_buffer(
                size=size, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST
            )
            self.geometry[key] = buffer
            started = time.monotonic()
            use_gpu = (
                self.packing_min_faces is not None
                and prepared.face_count >= self.packing_min_faces
            )
            if use_gpu and not self.packing_attempted:
                self.packing_attempted = True
                candidate = None
                try:
                    candidate = GeometryPacker(self.device, self.memory)
                    candidate.canary()
                    self.packer = candidate
                except Exception as exc:
                    # Shader/runtime failures decline this optional preparation
                    # path. Validated geometry can still use canonical CPU packing.
                    if candidate is not None:
                        candidate.close()
                    from app.core.logging import get_logger

                    get_logger(__name__).warning(
                        "GPU geometry packing unavailable: %s", exc
                    )
                    self.packing_min_faces = None
            if use_gpu and self.packer is not None:
                transferred = self.packer.transferred_bytes
                transfer_seconds = self.packer.transfer_seconds
                gpu_normals = self.packer.gpu_normal_uploads
                cpu_normals = self.packer.cpu_normal_fallbacks
                normal_seconds = self.packer.normal_seconds
                self.packer.submit(prepared, buffer, key)
                self.geometry_gpu_normal_uploads += (
                    self.packer.gpu_normal_uploads - gpu_normals
                )
                self.geometry_cpu_normal_fallbacks += (
                    self.packer.cpu_normal_fallbacks - cpu_normals
                )
                self.geometry_normal_seconds += (
                    self.packer.normal_seconds - normal_seconds
                )
                if key in self.packer.normal_statuses():
                    self.unverified_normals.add(key)
                self.transfers += self.packer.transferred_bytes - transferred
                self.transfer_seconds += self.packer.transfer_seconds - transfer_seconds
                self.gpu_geometry_uploads += 1
            else:
                if isinstance(prepared, DeferredRender):
                    normal_started = time.monotonic()
                    prepared = canonical(prepared)
                    self.geometry_normal_seconds += time.monotonic() - normal_started
                    self.geometry_cpu_normal_fallbacks += 1
                offset = 0
                for values in packed_chunks(prepared):
                    transfer_started = time.monotonic()
                    self.device.queue.write_buffer(buffer, offset, values)
                    self.transfer_seconds += time.monotonic() - transfer_started
                    offset += values.nbytes
                self.transfers += size
                self.cpu_geometry_uploads += 1
            self.geometry_packing_seconds += time.monotonic() - started
            self.uploads += 1
        except BaseException:
            try:
                if self.packer is not None:
                    self.packer.release_completed(fence_complete=False)
            finally:
                self.memory.unpin(key)
                self.memory.remove(key)
            raise
        self.memory.unpin(key)
        return buffer

    def attachments(self, width, height, size):
        import wgpu

        frame_bytes = self.readback_frame_bytes
        if frame_bytes is None:
            frame_bytes = ((width * 4 + 255) // 256) * 256 * height
        if (
            max(size, self.projected_size)
            + width * height * 8
            + max(
                self.readback_size,
                frame_bytes * self.readback_frames + self.readback_status_bytes,
            )
            + self.postprocess_size
            + 116
            > self.workspace_limit
        ):
            from app.runtime.compute.contracts import ComputeUnavailable, Reason

            raise ComputeUnavailable(Reason.CAPACITY)
        if self.size != (width, height):
            for texture in (self.color, self.depth):
                if texture is not None:
                    texture.destroy()
            self.color = self.device.create_texture(
                size=(width, height, 1),
                format="rgba8unorm",
                usage=wgpu.TextureUsage.RENDER_ATTACHMENT
                | wgpu.TextureUsage.COPY_SRC
                | wgpu.TextureUsage.TEXTURE_BINDING,
            )
            self.depth = self.device.create_texture(
                size=(width, height, 1),
                format="depth32float",
                usage=wgpu.TextureUsage.RENDER_ATTACHMENT,
            )
            self.size = width, height
        if size > self.projected_size:
            if self.projected is not None:
                self.projected.destroy()
            self.projected = self.device.create_buffer(
                size=size, usage=wgpu.BufferUsage.STORAGE
            )
            self.projected_size = size
        readback_size = frame_bytes * self.readback_frames + self.readback_status_bytes
        if readback_size > self.readback_size:
            if self.readback is not None:
                self.readback.destroy()
            self.readback = self.device.create_buffer(
                size=readback_size,
                usage=wgpu.BufferUsage.MAP_READ | wgpu.BufferUsage.COPY_DST,
            )
            self.readback_size = readback_size
        if self.uniform is None:
            self.uniform = self.device.create_buffer(
                size=112, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST
            )
            self.visible = self.device.create_buffer(
                size=4, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST
            )

    def draw(self, prepared, key, rotation, width, height, matte):
        objects = self.geometry[key]
        size = prepared.face_count * 96
        self.attachments(width, height, size)
        view = prepared.vertices @ rotation.T.astype(np.float32)
        low, high = view.min(axis=0), view.max(axis=0)
        scale = min(
            width
            * (1 - 2 * PREVIEW_PROFILE.margin_fraction)
            / max(float(high[0] - low[0]), 1e-6),
            height
            * (1 - 2 * PREVIEW_PROFILE.margin_fraction)
            / max(float(high[1] - low[1]), 1e-6),
        )
        rows = np.zeros((7, 4), dtype="<f4")
        rows[:3, :3] = rotation
        rows[3] = (
            (float(low[0]) + float(high[0])) * 0.5,
            (float(low[1]) + float(high[1])) * 0.5,
            scale,
            np.linalg.det(rotation),
        )
        rows[4] = (width, height, low[2], max(float(high[2] - low[2]), 1e-12))
        rows[5, 0] = 0.0 if matte else 0.22
        rows[6, :3] = PREVIEW_PROFILE.material_albedo
        self.device.queue.write_buffer(self.uniform, 0, rows)
        self.device.queue.write_buffer(self.visible, 0, bytes(4))
        entries = [
            {"binding": 0, "resource": {"buffer": objects}},
            {"binding": 1, "resource": {"buffer": self.projected, "size": size}},
            {"binding": 2, "resource": {"buffer": self.visible}},
            {"binding": 3, "resource": {"buffer": self.uniform}},
        ]
        group = self.device.create_bind_group(
            layout=self.compute.get_bind_group_layout(0), entries=entries
        )
        draw_group = self.device.create_bind_group(
            layout=self.pipeline.get_bind_group_layout(0), entries=entries[1:]
        )
        encoder = self.device.create_command_encoder()
        compute = encoder.begin_compute_pass()
        compute.set_pipeline(self.compute)
        compute.set_bind_group(0, group)
        compute.dispatch_workgroups((prepared.face_count + 63) // 64)
        compute.end()
        assert self.color is not None and self.depth is not None
        frame = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": self.color.create_view(),
                    "resolve_target": None,
                    "clear_value": (0, 0, 0, 0),
                    "load_op": "clear",
                    "store_op": "store",
                }
            ],
            depth_stencil_attachment={
                "view": self.depth.create_view(),
                "depth_clear_value": 1.0,
                "depth_load_op": "clear",
                "depth_store_op": "store",
            },
        )
        frame.set_pipeline(self.pipeline)
        frame.set_bind_group(0, draw_group)
        frame.draw(6, prepared.face_count)
        frame.end()
        if self.gpu_finalize_active:
            assert self.postprocessor is not None
            self.postprocessor.encode(
                encoder, self.color, self.readback, self.frame_offset
            )
        else:
            encoder.copy_texture_to_buffer(
                {"texture": self.color},
                {
                    "buffer": self.readback,
                    "offset": self.frame_offset,
                    "bytes_per_row": ((width * 4 + 255) // 256) * 256,
                    "rows_per_image": height,
                },
                (width, height, 1),
            )
        self.device.queue.submit([encoder.finish()])
        self.submissions += 1

    def geometry_key(self, payload, prepared):
        header_size = struct.unpack("!I", payload[:4])[0]
        return "geometry:" + identity(layout_for(prepared), payload[4 + header_size :])

    def execute(self, payload: bytes) -> bytes:
        return self.execute_many([payload])[0]

    def execute_many(self, payloads: list[bytes]) -> list[bytes]:
        """Upload admitted models once; submit all cameras before one map fence.

        All frames in a submission group share dimensions. Sequential queue
        writes/submissions retain each camera's uniforms while pooled textures
        and projected geometry are reused. Readbacks occupy disjoint slots.
        """
        decoded = [decode(payload) for payload in payloads]
        dimensions = {(width, height) for _, width, height, _, _ in decoded}
        if len(dimensions) != 1:
            raise ValueError("compute_batch_dimensions")
        keys = [
            hashlib.sha256(RASTERIZER_RECIPE.encode() + p).hexdigest() for p in payloads
        ]
        geometry_keys = [
            self.geometry_key(p, d[0]) for p, d in zip(payloads, decoded, strict=True)
        ]
        return self.execute_prepared_many(decoded, geometry_keys, keys)

    def execute_prepared_many(
        self, decoded, geometry_keys, keys, *, raw=False, gpu_finalize=False
    ):
        results, refused = self._execute_prepared_batch(
            decoded, geometry_keys, keys, raw=raw, gpu_finalize=gpu_finalize
        )
        if refused:
            # Keep healthy results, then hold CPU normals for only one refused
            # geometry at a time: host admission covers the largest scratch, not
            # a simultaneous canonical copy for every model in the batch.
            groups: dict[str, list[int]] = {}
            for index in refused:
                groups.setdefault(geometry_keys[index], []).append(index)
            for indices in groups.values():
                started = time.monotonic()
                cpu_prepared = canonical(decoded[indices[0]][0])
                self.geometry_cpu_normal_fallbacks += 1
                self.geometry_normal_seconds += time.monotonic() - started
                fallback = [(cpu_prepared, *decoded[index][1:]) for index in indices]
                recovered, still_refused = self._execute_prepared_batch(
                    fallback,
                    [geometry_keys[index] for index in indices],
                    [keys[index] for index in indices],
                    raw=raw,
                    gpu_finalize=gpu_finalize,
                )
                if still_refused:
                    raise RuntimeError("compute_canonical_normals_refused")
                results.update(
                    {index: recovered[offset] for offset, index in enumerate(indices)}
                )
                del fallback, cpu_prepared
        return [results[index] for index in range(len(decoded))]

    def _discard_unverified_geometry(self):
        for key in list(self.unverified_normals):
            if key in self.geometry:
                self.memory.remove(key)
            self.unverified_normals.discard(key)

    def _execute_prepared_batch(
        self, decoded, geometry_keys, keys, *, raw=False, gpu_finalize=False
    ):
        import wgpu

        dimensions = {(width, height) for _, width, height, _, _ in decoded}
        if len(dimensions) != 1:
            raise ValueError("compute_batch_dimensions")
        width, height = next(iter(dimensions))
        if gpu_finalize and not self.postprocess_attempted:
            self.postprocess_attempted = True
            from .gpu_postprocess import Postprocessor

            candidate = None
            try:
                candidate = Postprocessor(self.device)
                candidate.canary()
                self.postprocessor = candidate
            except Exception as exc:
                # A failed native postprocess canary keeps rasterization usable;
                # the already-admitted caller performs canonical CPU finalization.
                if candidate is not None:
                    candidate.close()
                from app.core.logging import get_logger

                get_logger(__name__).warning("GPU postprocess unavailable: %s", exc)
        self.gpu_finalize_active = gpu_finalize and self.postprocessor is not None
        raw = raw and not self.gpu_finalize_active
        keys = [
            ("gpu-final:" if self.gpu_finalize_active else "raw:" if raw else "final:")
            + key
            for key in keys
        ]
        missing = [i for i, key in enumerate(keys) if key not in self.outputs]
        frame_count = sum(len(decoded[i][3]) for i in missing)
        if frame_count > 8:
            from app.runtime.compute.contracts import ComputeUnavailable, Reason

            raise ComputeUnavailable(Reason.CAPACITY)
        factor = PREVIEW_PROFILE.supersample_for(width)
        w, h = width * factor, height * factor
        result_width, result_height = (
            (width, height) if self.gpu_finalize_active else (w, h)
        )
        pitch = width * 4 if self.gpu_finalize_active else ((w * 4 + 255) // 256) * 256
        frame_bytes = pitch * result_height
        self.readback_frame_bytes = frame_bytes
        status_keys = list(
            dict.fromkeys(
                geometry_keys[index]
                for index in missing
                if geometry_keys[index] in self.unverified_normals
                or (
                    isinstance(decoded[index][0], DeferredRender)
                    and geometry_keys[index] not in self.geometry
                )
            )
        )
        self.readback_status_bytes = len(status_keys) * 4
        if self.gpu_finalize_active:
            assert self.postprocessor is not None
            from .gpu_postprocess import workspace_bytes

            if self.postprocessor.size != (width, height, factor):
                self.postprocessor.close()
            self.postprocess_size = workspace_bytes(width, height, factor)
        pinned = []
        new_outputs = {}
        refused = []
        fence_complete = False
        try:
            self.readback_frames = max(1, frame_count)
            # Allocate the largest workspace first so later models cannot replace
            # the shared buffers while earlier commands are still pending.
            if missing:
                self.attachments(
                    w, h, max(decoded[i][0].face_count for i in missing) * 96
                )
            if missing and self.gpu_finalize_active:
                assert self.postprocessor is not None
                self.postprocessor.prepare(width, height, factor)
            for i in missing:
                prepared = decoded[i][0]
                key = geometry_keys[i]
                self.upload(prepared, key)
                self.memory.pin(key, time.monotonic())
                pinned.append(key)
            frame_index = 0
            spans = {}
            for i, key in zip(missing, pinned, strict=True):
                prepared, _, _, views, matte = decoded[i]
                spans[i] = (frame_index, len(views))
                for view in views:
                    rotation = (
                        core._select_view_rotation(prepared.vertices)
                        if view is None
                        else view
                    )
                    self.frame_offset = frame_index * frame_bytes
                    self.draw(prepared, key, rotation, w, h, matte)
                    frame_index += 1
            if missing:
                assert self.readback is not None
                statuses = (
                    self.packer.normal_statuses() if self.packer is not None else {}
                )
                if any(key in statuses for key in status_keys):
                    encoder = self.device.create_command_encoder()
                    for index, key in enumerate(status_keys):
                        if key in statuses:
                            encoder.copy_buffer_to_buffer(
                                statuses[key],
                                0,
                                self.readback,
                                frame_count * frame_bytes + index * 4,
                                4,
                            )
                    self.device.queue.submit([encoder.finish()])
                started = time.monotonic()
                self.readback.map_sync(wgpu.MapMode.READ)
                fence_complete = True
                try:
                    pixels = bytes(
                        self.readback.read_mapped(
                            size=frame_count * frame_bytes + self.readback_status_bytes
                        )
                    )
                finally:
                    self.readback.unmap()
                self.transfer_seconds += time.monotonic() - started
                rejected_keys = set()
                for index, key in enumerate(status_keys):
                    if key in statuses:
                        status = struct.unpack_from(
                            "<I", pixels, frame_count * frame_bytes + index * 4
                        )[0]
                        if status:
                            rejected_keys.add(key)
                        else:
                            self.unverified_normals.discard(key)
                refused = [
                    index for index in missing if geometry_keys[index] in rejected_keys
                ]
                self.readback_batches += 1
                self.batch_model_counts[len(missing) - 1] += 1
                self.batch_frame_counts[frame_count - 1] += 1
                for i in missing:
                    if i in refused:
                        continue
                    first, count = spans[i]
                    frames = []
                    for index in range(first, first + count):
                        rgba = (
                            np.frombuffer(
                                pixels,
                                dtype=np.uint8,
                                count=frame_bytes,
                                offset=index * frame_bytes,
                            )
                            .reshape(result_height, pitch)[:, : result_width * 4]
                            .copy()
                        )
                        frames.append(
                            rgba.tobytes()
                            if raw or self.gpu_finalize_active
                            else core.postprocess_rgba(
                                rgba.tobytes(), width, height
                            ).rgba
                        )
                    new_outputs[i] = struct.pack(
                        "!III", w if raw else width, h if raw else height, count
                    ) + b"".join(frames)
            results = {}
            for i, key in enumerate(keys):
                if i in refused:
                    continue
                if i in new_outputs:
                    result = new_outputs[i]
                else:
                    result = self.outputs[key]
                    self.outputs.move_to_end(key)
                    self.output_hits += 1
                results[i] = result
            # Bounded host RGBA cache avoids reshading repeated views. Its 32 MiB
            # ceiling is included in render_policy's host reservation.
            for i, result in new_outputs.items():
                if len(result) <= OUTPUT_CACHE_BYTES:
                    while (
                        self.outputs
                        and self.output_bytes + len(result) > OUTPUT_CACHE_BYTES
                    ):
                        _, retired = self.outputs.popitem(last=False)
                        self.output_bytes -= len(retired)
                    if keys[i] not in self.outputs:
                        self.outputs[keys[i]] = result
                        self.output_bytes += len(result)
            return results, refused
        finally:
            try:
                if self.packer is not None:
                    self.packer.release_completed(fence_complete=fence_complete)
            finally:
                self.readback_frames, self.frame_offset = 1, 0
                self.readback_frame_bytes = None
                self.readback_status_bytes = 0
                self.gpu_finalize_active = False
                for key in pinned:
                    self.memory.unpin(key)
                self._discard_unverified_geometry()

    def close(self):
        if self.packer is not None:
            try:
                self.packer.close()
            finally:
                # Renderer workspaces may be evicted and then reused. The old
                # packer owns a destroyed completion fence after close.
                self.packer = None
                self.packing_attempted = False
                self._discard_unverified_geometry()
        if self.postprocessor is not None:
            self.postprocessor.close()
        self.postprocess_size = 0
        self.outputs.clear()
        self.output_bytes = 0
        for resource in (
            self.color,
            self.depth,
            self.projected,
            self.uniform,
            self.visible,
            self.readback,
        ):
            if resource is not None:
                resource.destroy()
        self.color = self.depth = self.projected = self.uniform = self.visible = None
        self.readback = None
        self.readback_size = 0
        self.size = None
        self.projected_size = 0

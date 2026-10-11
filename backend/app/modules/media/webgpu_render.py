"""Persistent portable geometry, projection, culling, shading and rasterization.

The GPU owns visual framing, shading and rasterization. Only bounded validated
arrays enter this owner; authoritative geometry remains unchanged. Qualification must pass on the exact runtime,
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
from .gpu_compact import CompactGeometry
from .gpu_normals import DeferredRender, canonical
from .gpu_render_layout import (
    FRAME_BYTES,
    FRAME_STRIDE,
    MAX_FRAMES,
    VIEW_WORKSPACE_BYTES,
)
from .gpu_views import FRAME_STRUCT, ViewPipeline, frame_config

OUTPUT_CACHE_BYTES = 32 * 1024**2

SHADER = (
    FRAME_STRUCT
    + """
@group(0) @binding(0) var<storage, read> positions: array<f32>;
@group(0) @binding(1) var<storage, read> faces: array<u32>;
@group(0) @binding(2) var<storage, read> identities: array<u32>;
@group(0) @binding(3) var<storage, read> smoothed: array<f32>;
@group(0) @binding(4) var<uniform> frame: Frame;
fn point(index: u32) -> vec3<f32> {
    let p = index * 3u;
    return vec3(positions[p], positions[p+1u], positions[p+2u]);
}
fn unit(value: vec3<f32>) -> vec3<f32> {
    let scale = max(abs(value.x), max(abs(value.y), abs(value.z)));
    if scale == 0. { return vec3(0.); }
    let scaled = value / scale;
    return scaled / max(length(scaled), 1e-30);
}
fn rotate(value: vec3<f32>) -> vec3<f32> {
    return vec3(dot(value,frame.r0.xyz), dot(value,frame.r1.xyz), dot(value,frame.r2.xyz));
}
struct Vertex { @builtin(position) pos: vec4<f32>, @location(0) normal: vec3<f32> };
@vertex fn vs(@builtin(vertex_index) corner: u32, @builtin(instance_index) face: u32) -> Vertex {
    let offset = face * 3u;
    let a = point(faces[offset]); let b = point(faces[offset+1u]); let c = point(faces[offset+2u]);
    // Rescale edges before their cross product: rendering supports finite source
    // coordinates without overflowing/underflowing squared normal lengths.
    let flat = unit(cross(unit(b-a),unit(c-a)));
    let index = faces[offset+corner];
    let p = identities[index] * 3u;
    let blended = vec3(smoothed[p],smoothed[p+1u],smoothed[p+2u]);
    var weight = clamp((dot(blended,flat)-0.75)/0.17,0.,1.);
    weight = weight*weight*(3.-2.*weight);
    let normal = rotate(unit(weight*blended+(1.-weight)*flat));
    let position = rotate(point(index));
    let xy = (position.xy-frame.projection.xy)*frame.projection.z;
    var out: Vertex;
    out.pos = vec4(2.*xy.x/frame.extent.x, 2.*xy.y/frame.extent.y,
                  clamp((position.z-frame.extent.z)/frame.extent.w,0.,1.)*0.999999,1.);
    out.normal = select(-normal,normal,normal.z>=0.);
    return out;
}
fn shade(value: vec3<f32>) -> vec3<f32> {
    let n = unit(value);
    let key = normalize(vec3(-0.5,0.65,1.));
    let fill = normalize(vec3(0.55,-0.25,0.55));
    let half_vector = normalize(key+vec3(0.,0.,1.));
    let diffuse = (0.3+1.05*clamp(dot(n,key),0.,1.)*vec3(1.,0.98,0.95)
                       +0.3*clamp(dot(n,fill),0.,1.)*vec3(0.55,0.62,0.78))*frame.albedo.xyz;
    let fresnel = pow(1.-clamp(n.z,0.,1.),3.);
    let specular = frame.style.x*pow(clamp(dot(n,half_vector),0.,1.),32.);
    return clamp(diffuse+0.22*fresnel*vec3(0.85,0.92,1.)+specular,vec3(0.),vec3(1.));
}
@fragment fn fs(in: Vertex) -> @location(0) vec4<f32> {
    return vec4(floor(255.*shade(in.normal))/255.,1.);
}
"""
)


class Renderer:
    def __init__(
        self,
        device,
        memory: Residency | None = None,
        *,
        packing_min_faces: int | None = None,
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
        # Retain the old constructor argument for callers of the previous
        # packer. Compact geometry applies to every model, so no packing
        # threshold is advertised; normal preparation has its own eligibility.
        self.packing_min_faces = None
        self.packer = None
        self.packing_attempted = False
        self.gpu_geometry_uploads = self.cpu_geometry_uploads = 0
        self.geometry_packing_seconds = 0.0
        # GPU normal submissions include numerical refusals; fallback count
        # reports the subset retried with canonical CPU preparation.
        self.geometry_gpu_normal_uploads = self.geometry_cpu_normal_fallbacks = 0
        self.geometry_normal_seconds = 0.0
        self.upload_calls = self.bounds_vertices = 0
        self.geometry_local_normal_repairs = 0
        self.views = ViewPipeline(device)
        shader = device.create_shader_module(code=SHADER)
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
        if key in self.geometry:
            self.cache_hits += 1
            return self.geometry[key]
        if self.packer is None:
            self.packer = CompactGeometry(self.device, self.memory, self.views)
            self.packer.geometry = self.geometry
        self.unverified_normals.add(key)
        started = time.monotonic()
        buffer = self.packer.acquire(prepared, key)
        self.geometry_packing_seconds += time.monotonic() - started
        self.uploads += 1
        self.gpu_geometry_uploads += 1
        return buffer

    def attachments(self, width, height, size):
        import wgpu

        frame_bytes = self.readback_frame_bytes
        if frame_bytes is None:
            frame_bytes = ((width * 4 + 255) // 256) * 256 * height
        if (
            width * height * 8
            + max(
                self.readback_size,
                frame_bytes * self.readback_frames + self.readback_status_bytes,
            )
            + self.postprocess_size
            + VIEW_WORKSPACE_BYTES
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
                size=FRAME_STRIDE * MAX_FRAMES,
                usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.STORAGE,
            )
            self.visible = self.device.create_buffer(
                size=FRAME_STRIDE * MAX_FRAMES,
                usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST,
            )

    def draw(self, encoder, prepared, key, frame_index, width, height):
        geometry = self.geometry[key]
        self.views.encode_camera(
            encoder, geometry.bounds, self.visible, self.uniform, frame_index
        )
        entries = [
            {"binding": index, "resource": {"buffer": buffer}}
            for index, buffer in enumerate(
                (
                    geometry.positions,
                    geometry.faces,
                    geometry.identities,
                    geometry.smoothed,
                )
            )
        ]
        entries.append(
            {
                "binding": 4,
                "resource": {
                    "buffer": self.uniform,
                    "offset": frame_index * FRAME_STRIDE,
                    "size": FRAME_BYTES,
                },
            }
        )
        draw_group = self.device.create_bind_group(
            layout=self.pipeline.get_bind_group_layout(0), entries=entries
        )
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
        frame.draw(3, prepared.face_count)
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

    def geometry_key(self, payload, prepared):
        header_size = struct.unpack("!I", payload[:4])[0]
        return "geometry:" + identity(layout_for(prepared), payload[4 + header_size :])

    def execute(self, payload: bytes) -> bytes:
        return self.execute_many([payload])[0]

    def execute_many(self, payloads: list[bytes]) -> list[bytes]:
        """Upload admitted models once; submit all cameras before one map fence.

        All frames share dimensions, one staging upload and one submission.
        Cameras and readbacks occupy distinct slots; attachments are reused.
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
            if key in self.memory.entries:
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
                    (
                        isinstance(decoded[index][0], DeferredRender)
                        or getattr(decoded[index][0], "normal_mode", "")
                        == "gpu-visual-v2"
                    )
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
                self.attachments(w, h, 0)
                if self.packer is None:
                    self.packer = CompactGeometry(self.device, self.memory, self.views)
                    self.packer.geometry = self.geometry
            if missing and self.gpu_finalize_active:
                assert self.postprocessor is not None
                self.postprocessor.prepare(width, height, factor)
            if missing:
                assert self.packer is not None
                before = (
                    self.packer.transferred_bytes,
                    self.packer.transfer_seconds,
                    self.packer.gpu_normal_uploads,
                    self.packer.cpu_normal_fallbacks,
                    self.packer.normal_seconds,
                    self.packer.upload_calls,
                    self.packer.bounds_vertices,
                )
            for i in missing:
                prepared = decoded[i][0]
                key = geometry_keys[i]
                self.upload(prepared, key)
                self.memory.pin(key, time.monotonic())
                pinned.append(key)
            frame_index = 0
            spans = {}
            if missing:
                assert self.packer is not None and self.readback is not None
                configs = b"".join(
                    frame_config(view, w, h, decoded[i][4])
                    for i in missing
                    for view in decoded[i][3]
                )
                encoder = self.device.create_command_encoder()
                self.packer.encode(encoder, self.visible, configs)
                self.transfers += self.packer.transferred_bytes - before[0]
                self.transfer_seconds += self.packer.transfer_seconds - before[1]
                self.geometry_gpu_normal_uploads += (
                    self.packer.gpu_normal_uploads - before[2]
                )
                self.geometry_cpu_normal_fallbacks += (
                    self.packer.cpu_normal_fallbacks - before[3]
                )
                self.geometry_normal_seconds += self.packer.normal_seconds - before[4]
                self.upload_calls += self.packer.upload_calls - before[5]
                self.bounds_vertices += self.packer.bounds_vertices - before[6]
                for i, key in zip(missing, pinned, strict=True):
                    prepared, _, _, views, _ = decoded[i]
                    spans[i] = (frame_index, len(views))
                    for _ in views:
                        self.frame_offset = frame_index * frame_bytes
                        self.draw(encoder, prepared, key, frame_index, w, h)
                        frame_index += 1
                statuses = self.packer.normal_statuses()
                telemetry_keys = self.packer.telemetry_keys()
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
                self.submissions += 1
                started = time.monotonic()
                self.readback.map_sync(wgpu.MapMode.READ)
                fence_complete = True
                try:
                    pixels = bytes(
                        self.readback.read_mapped(
                            size=frame_count * frame_bytes + self.readback_status_bytes,
                            copy=False,
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
                        if key in telemetry_keys:
                            self.geometry_local_normal_repairs += status
                        elif status:
                            rejected_keys.add(key)
                self.unverified_normals.difference_update(
                    set(geometry_keys[i] for i in missing) - rejected_keys
                )
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

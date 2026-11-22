"""Persistent portable geometry, projection, culling, shading and rasterization.

The GPU owns visual framing, shading and rasterization. Only bounded validated
arrays enter this owner; authoritative geometry remains unchanged. Qualification must pass on the exact runtime,
recipe and physical adapter before the broker can select it.
"""

import hashlib
import struct
import time
from collections import OrderedDict
from typing import Any

import numpy as np
from printstash_core.mesh import rasterizer as core
from printstash_core.mesh.preview_profile import (
    PREVIEW_PROFILE,
    preview_appearance,
    preview_rasterizer_recipe,
)

from app.runtime.compute.budget import Residency

from .compute_geometry import decode, identity, layout_for
from .gpu_compact import CompactGeometry
from .gpu_render_layout import (
    FRAME_BYTES,
    FRAME_STRIDE,
    MAX_FRAMES,
    VIEW_WORKSPACE_BYTES,
    bounds_scratch_sizes,
    view_bounds_sizes,
)
from .gpu_views import FRAME_STRUCT, ViewPipeline, frame_config

OUTPUT_CACHE_BYTES = 32 * 1024**2

SHADER = (
    FRAME_STRUCT
    + """
@group(0) @binding(0) var<storage, read> positions: array<f32>;
@group(0) @binding(1) var<storage, read> faces: array<u32>;
@group(0) @binding(2) var<storage, read> corner_normals: array<f32>;
@group(0) @binding(3) var<uniform> frame: Frame;
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
struct Vertex { @builtin(position) pos: vec4<f32>, @location(0) normal: vec3<f32> };
@vertex fn vs(@builtin(vertex_index) corner: u32, @builtin(instance_index) face: u32) -> Vertex {
    let offset = face * 3u;
    let index = faces[offset+corner];
    let p = (offset+corner) * 3u;
    let normal = rotate(vec3(corner_normals[p],corner_normals[p+1u],corner_normals[p+2u]), frame);
    let position = rotate(point(index), frame);
    let xy = (position.xy-frame.projection.xy)*frame.projection.z;
    var out: Vertex;
    out.pos = vec4(2.*xy.x/frame.extent.x, 2.*xy.y/frame.extent.y,
                  clamp((position.z-frame.extent.z)/frame.extent.w,0.,1.)*0.999999,1.);
    // Preserve screen X/Y when reflecting depth into the lighting frame.
    out.normal = select(-normal,normal,normal.z>=0.);
    if frame.studio != 0. { out.normal = vec3(-out.normal.xy,out.normal.z); }
    return out;
}
fn shade(value: vec3<f32>) -> vec3<f32> {
    let n = unit(value);
    let key = normalize(frame.key_direction.xyz);
    let fill = normalize(frame.fill_direction.xyz);
    let half_vector = normalize(key+vec3(0.,0.,1.));
    let diffuse = (frame.ambient + frame.key_strength*clamp(dot(n,key),0.,1.)*frame.key_colour
                   + frame.fill_strength*clamp(dot(n,fill),0.,1.)*frame.fill_colour)*frame.albedo;
    let fresnel = pow(1.-clamp(n.z,0.,1.),frame.rim_power);
    let specular = frame.specular_strength*pow(clamp(dot(n,half_vector),0.,1.),frame.specular_power);
    return clamp(diffuse+frame.rim_strength*fresnel*frame.rim_colour+specular,vec3(0.),vec3(1.));
}
@fragment fn fs(in: Vertex) -> @location(0) vec4<f32> {
    return vec4(floor(255.*shade(in.normal))/255.,1.);
}
"""
)


GRID_SHADER = (
    FRAME_STRUCT
    + """
@group(0) @binding(0) var<uniform> frame: Frame;
@vertex fn grid_vs(@builtin(vertex_index) index: u32) -> @builtin(position) vec4<f32> {
    let points = array<vec2<f32>, 3>(vec2(-1., -1.), vec2(3., -1.), vec2(-1., 3.));
    return vec4(points[index], 1., 1.);
}
@fragment fn grid_fs(@builtin(position) pixel: vec4<f32>) -> @location(0) vec4<f32> {
    let depth = frame.r2.xyz;
    if frame.floor.w == 0. || depth.z >= -1e-6 { discard; }
    let size = frame.extent.xy;
    let xy = vec2(pixel.x-size.x*0.5, size.y*0.5-pixel.y)/frame.projection.z
             + frame.projection.xy;
    let right = frame.r0.xyz - depth*frame.r0.z/depth.z;
    let up = frame.r1.xyz - depth*frame.r1.z/depth.z;
    let point = depth*frame.floor.x/depth.z + right*xy.x + up*xy.y;
    let cell = frame.floor.y;
    let offset = abs(fract(point/cell+0.5)-0.5)*cell;
    let step = max(sqrt(right*right+up*up)*frame.floor.w/frame.projection.z, vec3(1e-6));
    let line = clamp(0.95-min(offset.x/step.x, offset.y/step.y),0.,1.);
    let radial = length((point.xy-frame.footprint.xy)/frame.footprint.zw);
    let screen = abs(2.*(pixel.xy/size-0.5));
    let edge = max(screen.x, screen.y);
    let fade = pow(clamp((1.-radial)/0.6,0.,1.),1.1) * clamp((0.99-edge)/0.11,0.,1.);
    let alpha = floor(line*fade*0.8*255.)/255.;
    if alpha == 0. { discard; }
    return vec4(vec3(138.,147.,166.)/255., alpha);
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
        self.pending_uploads: set[str] = set()
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
        # Retain the GPU status fields exposed by the compute API.
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
        grid = device.create_shader_module(code=GRID_SHADER)
        self.grid_pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": grid, "entry_point": "grid_vs", "buffers": []},
            depth_stencil={
                "format": "depth32float",
                "depth_write_enabled": False,
                "depth_compare": "always",
            },
            fragment={
                "module": grid,
                "entry_point": "grid_fs",
                "targets": [{"format": "rgba8unorm"}],
            },
        )
        self.color = self.depth = self.projected = self.uniform = self.visible = None
        self.view_bounds: list[Any] = []
        self.view_bounds_size = 0
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

    def upload(self, prepared, key):
        if key in self.geometry:
            self.cache_hits += 1
            return self.geometry[key]
        if self.packer is None:
            self.packer = CompactGeometry(self.device, self.memory, self.views)
            self.packer.geometry = self.geometry
        self.pending_uploads.add(key)
        started = time.monotonic()
        buffer = self.packer.acquire(prepared, key)
        self.geometry_packing_seconds += time.monotonic() - started
        self.uploads += 1
        self.gpu_geometry_uploads += 1
        return buffer

    def attachments(self, width, height, vertex_count):
        import wgpu

        bound_sizes = view_bounds_sizes(vertex_count)
        bounds_bytes = sum(bound_sizes)
        frame_bytes = self.readback_frame_bytes
        if frame_bytes is None:
            frame_bytes = ((width * 4 + 255) // 256) * 256 * height
        if (
            width * height * 8
            + max(
                self.readback_size,
                frame_bytes * self.readback_frames,
            )
            + self.postprocess_size
            + VIEW_WORKSPACE_BYTES
            + max(self.view_bounds_size, bounds_bytes)
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
        readback_size = frame_bytes * self.readback_frames
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

        if bounds_bytes > self.view_bounds_size:
            self._destroy_view_bounds()
            try:
                for size in bound_sizes:
                    self.view_bounds.append(
                        self.device.create_buffer(
                            size=size, usage=wgpu.BufferUsage.STORAGE
                        )
                    )
            except BaseException:
                self._destroy_view_bounds()
                raise
            self.view_bounds_size = bounds_bytes

    def _destroy_view_bounds(self):
        for buffer in self.view_bounds:
            buffer.destroy()
        self.view_bounds.clear()
        self.view_bounds_size = 0

    def draw(self, encoder, prepared, key, frame_index, width, height, *, grid=False):
        geometry = self.geometry[key]
        vertex_count = len(prepared.vertices)
        levels = len(bounds_scratch_sizes(vertex_count))
        self.views.encode_camera(
            encoder,
            geometry.bounds,
            geometry.positions,
            vertex_count,
            self.view_bounds[0],
            self.view_bounds[1 : 1 + levels],
            self.visible,
            self.uniform,
            frame_index,
        )
        entries = [
            {"binding": index, "resource": {"buffer": buffer}}
            for index, buffer in enumerate(
                (
                    geometry.positions,
                    geometry.faces,
                    geometry.corner_normals,
                )
            )
        ]
        entries.append(
            {
                "binding": 3,
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
        if grid and geometry.has_surface:
            grid_group = self.device.create_bind_group(
                layout=self.grid_pipeline.get_bind_group_layout(0),
                entries=[{"binding": 0, "resource": entries[-1]["resource"]}],
            )
            frame.set_pipeline(self.grid_pipeline)
            frame.set_bind_group(0, grid_group)
            frame.draw(3)
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
            hashlib.sha256(
                preview_rasterizer_recipe(matte=matte).encode() + payload
            ).hexdigest()
            for payload, (_, _, _, _, matte) in zip(payloads, decoded, strict=True)
        ]
        geometry_keys = [
            self.geometry_key(p, d[0]) for p, d in zip(payloads, decoded, strict=True)
        ]
        return self.execute_prepared_many(decoded, geometry_keys, keys)

    def execute_prepared_many(
        self, decoded, geometry_keys, keys, *, raw=False, gpu_finalize=False
    ):
        results = self._execute_prepared_batch(
            decoded, geometry_keys, keys, raw=raw, gpu_finalize=gpu_finalize
        )
        return [results[index] for index in range(len(decoded))]

    def _discard_pending_geometry(self):
        for key in list(self.pending_uploads):
            if key in self.memory.entries:
                self.memory.remove(key)
            self.pending_uploads.discard(key)

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
        if self.gpu_finalize_active:
            assert self.postprocessor is not None
            from .gpu_postprocess import workspace_bytes

            if self.postprocessor.size != (width, height, factor):
                self.postprocessor.close()
            self.postprocess_size = workspace_bytes(width, height, factor)
        pinned = []
        new_outputs = {}
        fence_complete = False
        try:
            self.readback_frames = max(1, frame_count)
            # Allocate the largest workspace first so later models cannot replace
            # the shared buffers while earlier commands are still pending.
            if missing:
                self.attachments(
                    w, h, max(len(decoded[i][0].vertices) for i in missing)
                )
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
                    frame_config(view, w, h, decoded[i][4], samples=factor)
                    for i in missing
                    for view in decoded[i][3]
                )
                encoder = self.device.create_command_encoder()
                self.packer.encode(encoder, self.visible, configs)
                self.transfers += self.packer.transferred_bytes - before[0]
                self.transfer_seconds += self.packer.transfer_seconds - before[1]
                self.geometry_normal_seconds += self.packer.normal_seconds - before[2]
                self.upload_calls += self.packer.upload_calls - before[3]
                self.bounds_vertices += self.packer.bounds_vertices - before[4]
                for i, key in zip(missing, pinned, strict=True):
                    prepared, _, _, views, matte = decoded[i]
                    spans[i] = (frame_index, len(views))
                    for _ in views:
                        self.frame_offset = frame_index * frame_bytes
                        self.draw(
                            encoder,
                            prepared,
                            key,
                            frame_index,
                            w,
                            h,
                            grid=not matte and preview_appearance().floor_grid,
                        )
                        frame_index += 1
                self.device.queue.submit([encoder.finish()])
                self.submissions += 1
                started = time.monotonic()
                self.readback.map_sync(wgpu.MapMode.READ)
                fence_complete = True
                try:
                    pixels = bytes(
                        self.readback.read_mapped(
                            size=frame_count * frame_bytes,
                            copy=False,
                        )
                    )
                finally:
                    self.readback.unmap()
                self.transfer_seconds += time.monotonic() - started
                self.pending_uploads.difference_update(pinned)
                self.readback_batches += 1
                self.batch_model_counts[len(missing) - 1] += 1
                self.batch_frame_counts[frame_count - 1] += 1
                for i in missing:
                    first, count = spans[i]
                    header = struct.pack(
                        "!III", w if raw else width, h if raw else height, count
                    )
                    if (raw or self.gpu_finalize_active) and pitch == result_width * 4:
                        # pixels owns immutable bytes copied before unmap. Copy
                        # this model's contiguous span directly into its reply;
                        # never retain the mapped buffer.
                        span = memoryview(pixels)[
                            first * frame_bytes : (first + count) * frame_bytes
                        ]
                        new_outputs[i] = b"".join((header, span))
                        continue
                    frames = [header]
                    for index in range(first, first + count):
                        rgba = (
                            np.frombuffer(
                                pixels,
                                dtype=np.uint8,
                                count=frame_bytes,
                                offset=index * frame_bytes,
                            )
                            .reshape(result_height, pitch)[:, : result_width * 4]
                            .tobytes()
                        )
                        frames.append(
                            rgba
                            if raw or self.gpu_finalize_active
                            else core.postprocess_rgba(rgba, width, height).rgba
                        )
                    new_outputs[i] = b"".join(frames)
            results = {}
            for i, key in enumerate(keys):
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
            return results
        finally:
            try:
                if self.packer is not None:
                    self.packer.release_completed(fence_complete=fence_complete)
            finally:
                self.readback_frames, self.frame_offset = 1, 0
                self.readback_frame_bytes = None
                self.gpu_finalize_active = False
                for key in pinned:
                    self.memory.unpin(key)
                self._discard_pending_geometry()

    def close(self):
        if self.packer is not None:
            try:
                self.packer.close()
            finally:
                # Renderer workspaces may be evicted and then reused. The old
                # packer owns a destroyed completion fence after close.
                self.packer = None
                self.packing_attempted = False
                self._discard_pending_geometry()
        if self.postprocessor is not None:
            self.postprocessor.close()
        self.postprocess_size = 0
        self._destroy_view_bounds()
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

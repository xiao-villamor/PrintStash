"""Portable separable Lanczos and vignette before readback.

The integer filter precision and premultiplied-alpha rounding match the pinned
Pillow CPU codec. Coefficients and the canonical float32 vignette are prepared
once per resolution; pixels never cross to the CPU for this operation.
"""

import math

import numpy as np

SHADER = """
struct Filter { start: u32, count: u32, weights: array<i32,13> };
@group(0) @binding(0) var source: texture_2d<f32>;
@group(0) @binding(1) var<storage, read_write> horizontal: array<u32>;
@group(0) @binding(2) var<storage, read_write> output_pixels: array<u32>;
@group(0) @binding(3) var<storage, read> xfilters: array<Filter>;
@group(0) @binding(4) var<storage, read> yfilters: array<Filter>;
@group(0) @binding(5) var<storage, read> vignette: array<f32>;
@group(0) @binding(6) var<uniform> size: vec4<u32>;
fn unpack(pixel: u32) -> vec4<i32> {
    return vec4<i32>(i32(pixel & 255u), i32((pixel >> 8u) & 255u),
                     i32((pixel >> 16u) & 255u), i32(pixel >> 24u));
}
fn pack(pixel: vec4<i32>) -> u32 {
    let p=vec4<u32>(pixel);
    return p.x | (p.y<<8u) | (p.z<<16u) | (p.w<<24u);
}
fn rounded(value: vec4<i32>) -> vec4<i32> {
    return clamp(value >> vec4<u32>(22u),vec4<i32>(0),vec4<i32>(255));
}
@compute @workgroup_size(8,8)
fn xpass(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x>=size.x || id.y>=size.z { return; }
    let kernel=xfilters[id.x];
    var sum=vec4<i32>(2097152);
    for(var i=0u;i<kernel.count;i++) {
        var p=vec4<i32>(round(textureLoad(source,vec2<i32>(i32(kernel.start+i),i32(id.y)),0)*255.));
        if size.w>1u {
            let t=p.xyz*p.w+vec3<i32>(128);
            p=vec4<i32>((t+(t>>vec3<u32>(8u)))>>vec3<u32>(8u),p.w);
        }
        sum+=p*kernel.weights[i];
    }
    horizontal[id.y*size.x+id.x]=pack(rounded(sum));
}
@compute @workgroup_size(8,8)
fn ypass(@builtin(global_invocation_id) id: vec3<u32>) {
    if id.x>=size.x || id.y>=size.y { return; }
    let kernel=yfilters[id.y];
    var sum=vec4<i32>(2097152);
    for(var i=0u;i<kernel.count;i++) {
        sum+=unpack(horizontal[(kernel.start+i)*size.x+id.x])*kernel.weights[i];
    }
    var p=rounded(sum);
    if size.w>1u && p.w>0 && p.w<255 {
        p=vec4<i32>(min(p.xyz*255/p.w,vec3<i32>(255)),p.w);
    }
    let index=id.y*size.x+id.x;
    p=vec4<i32>(vec3<i32>(vec3<f32>(p.xyz)*vignette[index]),p.w);
    output_pixels[index]=pack(p);
}
"""


def coefficients(length: int, factor: int) -> np.ndarray:
    if not 1 <= length <= 1280 or factor not in (1, 2):
        raise ValueError("compute_postprocess_dimensions")
    result = np.zeros((length, 15), dtype="<i4")
    for index in range(length):
        if factor == 1:
            result[index, :3] = (index, 1, 1 << 22)
            continue
        center = (index + 0.5) * factor
        start = max(0, int(center - 3 * factor + 0.5))
        stop = min(length * factor, int(center + 3 * factor + 0.5))
        values = []
        for sample in range(start, stop):
            distance = (sample + 0.5 - center) / factor
            if distance == 0:
                value = 1.0
            elif -3 <= distance < 3:
                angle = math.pi * distance
                value = (math.sin(angle) / angle) * (math.sin(angle / 3) / (angle / 3))
            else:
                value = 0.0
            values.append(value)
        total = sum(values)
        result[index, :2] = (start, len(values))
        result[index, 2 : 2 + len(values)] = [
            int(v / total * (1 << 22) + (0.5 if v >= 0 else -0.5)) for v in values
        ]
    return result


def workspace_bytes(width: int, height: int, factor: int) -> int:
    return width * height * (factor + 2) * 4 + (width + height) * 60 + 16


class Postprocessor:
    def __init__(self, device):
        self.device = device
        self.buffers = []
        self.size = None
        shader = device.create_shader_module(code=SHADER)
        self.xpipeline = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "xpass"}
        )
        self.ypipeline = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "ypass"}
        )

    def prepare(self, width, height, factor):
        import wgpu

        if self.size == (width, height, factor):
            return
        self.close()
        self.size = width, height, factor

        def buffer(data=None, size=None, usage=wgpu.BufferUsage.STORAGE):
            if data is None:
                item = self.device.create_buffer(size=size, usage=usage)
            else:
                item = self.device.create_buffer_with_data(data=data, usage=usage)
            self.buffers.append(item)
            return item

        self.horizontal = buffer(size=width * height * factor * 4)
        self.output = buffer(
            size=width * height * 4,
            usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC,
        )
        self.xfilters = buffer(coefficients(width, factor))
        self.yfilters = buffer(coefficients(height, factor))
        # Same float32 expression as core; uploaded once, with no per-image CPU work.
        x, y = np.meshgrid(
            np.linspace(-1, 1, width, dtype=np.float32),
            np.linspace(-1, 1, height, dtype=np.float32),
        )
        self.vignette = buffer(1.0 - 0.18 * np.clip(x**2 + y**2, 0, 1))
        self.uniform = buffer(
            np.asarray([width, height, height * factor, factor], dtype="<u4"),
            usage=wgpu.BufferUsage.UNIFORM,
        )

    def encode(self, encoder, source, destination, offset):
        width, height, factor = self.size
        xentries = [
            {"binding": 0, "resource": source.create_view()},
            {"binding": 1, "resource": {"buffer": self.horizontal}},
            {"binding": 3, "resource": {"buffer": self.xfilters}},
            {"binding": 6, "resource": {"buffer": self.uniform}},
        ]
        yentries = [
            {"binding": 1, "resource": {"buffer": self.horizontal}},
            {"binding": 2, "resource": {"buffer": self.output}},
            {"binding": 4, "resource": {"buffer": self.yfilters}},
            {"binding": 5, "resource": {"buffer": self.vignette}},
            {"binding": 6, "resource": {"buffer": self.uniform}},
        ]
        for pipeline, entries, rows in [
            (self.xpipeline, xentries, height * factor),
            (self.ypipeline, yentries, height),
        ]:
            group = self.device.create_bind_group(
                layout=pipeline.get_bind_group_layout(0), entries=entries
            )
            compute = encoder.begin_compute_pass()
            compute.set_pipeline(pipeline)
            compute.set_bind_group(0, group)
            compute.dispatch_workgroups((width + 7) // 8, (rows + 7) // 8)
            compute.end()
        encoder.copy_buffer_to_buffer(
            self.output, 0, destination, offset, width * height * 4
        )

    def canary(self):
        import wgpu
        from printstash_core.mesh.rasterizer import postprocess_rgba

        for width, height, factor in [(17, 13, 2), (641, 3, 1)]:
            self.prepare(width, height, factor)
            pixels = np.random.default_rng(0).integers(
                0, 256, (height * factor, width * factor, 4), dtype=np.uint8
            )
            source = self.device.create_texture(
                size=(width * factor, height * factor, 1),
                format="rgba8unorm",
                usage=wgpu.TextureUsage.COPY_DST | wgpu.TextureUsage.TEXTURE_BINDING,
            )
            output = self.device.create_buffer(
                size=width * height * 4,
                usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ,
            )
            try:
                self.device.queue.write_texture(
                    {"texture": source},
                    pixels,
                    {"bytes_per_row": width * factor * 4},
                    (width * factor, height * factor, 1),
                )
                encoder = self.device.create_command_encoder()
                self.encode(encoder, source, output, 0)
                self.device.queue.submit([encoder.finish()])
                output.map_sync(wgpu.MapMode.READ)
                try:
                    actual = bytes(output.read_mapped())
                finally:
                    output.unmap()
                expected = postprocess_rgba(pixels.tobytes(), width, height).rgba
                if actual != expected:
                    raise ValueError("compute_postprocess_canary")
            finally:
                source.destroy()
                output.destroy()
        self.close()

    def close(self):
        for buffer in self.buffers:
            buffer.destroy()
        self.buffers.clear()
        self.size = None

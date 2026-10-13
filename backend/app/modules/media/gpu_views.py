"""GPU bounds reduction and constant-size camera preparation per view.

Framing rotates eight corners of the cached AABB; source geometry and exact
measurements are untouched. No vertex array crosses back to the CPU.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

from .gpu_render_layout import FRAME_BYTES, FRAME_STRIDE

BOUNDS_COMMON = """
struct Bounds { low: vec4<f32>, high: vec4<f32> };
@group(0) @binding(1) var<storage, read_write> result: array<Bounds>;
var<workgroup> lows: array<vec3<f32>, 64>;
var<workgroup> highs: array<vec3<f32>, 64>;
fn reduce(local: u32, group: u32) {
    var step = 32u;
    loop {
        workgroupBarrier();
        if local < step {
            lows[local] = min(lows[local], lows[local+step]);
            highs[local] = max(highs[local], highs[local+step]);
        }
        if step == 1u { break; }
        step = step / 2u;
    }
    if local == 0u && group < arrayLength(&result) {
        result[group] = Bounds(vec4(lows[0], 0.), vec4(highs[0], 0.));
    }
}
"""
BOUNDS_VERTICES = (
    BOUNDS_COMMON
    + """
@group(0) @binding(0) var<storage, read> source: array<f32>;
@compute @workgroup_size(64)
fn bounds(@builtin(local_invocation_index) local: u32,
          @builtin(workgroup_id) work: vec3<u32>,
          @builtin(num_workgroups) groups: vec3<u32>) {
    let group = work.x + work.y * groups.x;
    let index = group * 64u + local;
    lows[local] = vec3(3.402823e38); highs[local] = vec3(-3.402823e38);
    if index < arrayLength(&source) / 3u {
        let offset = index * 3u;
        let value = vec3(source[offset], source[offset+1u], source[offset+2u]);
        lows[local] = value; highs[local] = value;
    }
    reduce(local, group);
}
"""
)
BOUNDS_REDUCE = (
    BOUNDS_COMMON
    + """
@group(0) @binding(0) var<storage, read> source: array<Bounds>;
@compute @workgroup_size(64)
fn bounds(@builtin(local_invocation_index) local: u32,
          @builtin(workgroup_id) work: vec3<u32>,
          @builtin(num_workgroups) groups: vec3<u32>) {
    let group = work.x + work.y * groups.x;
    let index = group * 64u + local;
    lows[local] = vec3(3.402823e38); highs[local] = vec3(-3.402823e38);
    if index < arrayLength(&source) {
        lows[local] = source[index].low.xyz; highs[local] = source[index].high.xyz;
    }
    reduce(local, group);
}
"""
)
FRAME_STRUCT = """
struct Frame {
    r0: vec4<f32>, r1: vec4<f32>, r2: vec4<f32>,
    projection: vec4<f32>, extent: vec4<f32>, style: vec4<f32>, albedo: vec4<f32>
};
"""
FRAME_SHADER = (
    FRAME_STRUCT
    + """
struct Bounds { low: vec4<f32>, high: vec4<f32> };
@group(0) @binding(0) var<storage, read> bounds: Bounds;
@group(0) @binding(1) var<uniform> config: Frame;
@group(0) @binding(2) var<storage, read_write> frame: Frame;
AUTO_ROTATIONS
@compute @workgroup_size(1)
fn camera() {
    var r0 = config.r0; var r1 = config.r1; var r2 = config.r2;
    if config.projection.w != 0. {
        let extents = bounds.high.xyz - bounds.low.xyz;
        var axis = 0u;
        if extents.y < extents.x { axis = 1u; }
        if extents.z < extents[axis] { axis = 2u; }
        let broad = max(extents[(axis+1u)%3u], extents[(axis+2u)%3u]);
        var choice = 3u;
        if broad > 1e-6 && extents[axis] / broad <= FLAT_RATIO { choice = axis; }
        r0 = rotations[choice*3u]; r1 = rotations[choice*3u+1u];
        r2 = rotations[choice*3u+2u];
    }
    var low = vec3(3.402823e38); var high = vec3(-3.402823e38);
    for (var index = 0u; index < 8u; index++) {
        let point = select(bounds.low.xyz, bounds.high.xyz,
                           vec3((index&1u)!=0u, (index&2u)!=0u, (index&4u)!=0u));
        let rotated = vec3(dot(point,r0.xyz), dot(point,r1.xyz), dot(point,r2.xyz));
        low = min(low,rotated); high = max(high,rotated);
    }
    let extent = max(high-low, vec3(1e-6));
    let scale = min(config.projection.x/extent.x, config.projection.y/extent.y)
                * (1. - 2.*config.projection.z);
    frame.r0 = r0; frame.r1 = r1; frame.r2 = r2;
    frame.projection = vec4(low.xy*0.5+high.xy*0.5, scale, 0.);
    frame.extent = vec4(config.projection.xy, low.z, extent.z);
    frame.style = config.style; frame.albedo = config.albedo;
}
"""
)


def _auto_rotations() -> str:
    angle = math.radians(PREVIEW_PROFILE.flat_tilt_degrees)
    tilt = np.array(
        [
            [1, 0, 0],
            [0, math.cos(angle), -math.sin(angle)],
            [0, math.sin(angle), math.cos(angle)],
        ]
    )
    bases = (
        np.array([[0, 1, 0], [0, 0, 1], [-1, 0, 0]]),
        np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]]),
        np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]]),
    )
    rotations = [tilt @ base for base in bases]
    azimuth = math.radians(PREVIEW_PROFILE.hero_azimuth_degrees)
    elevation = math.radians(-PREVIEW_PROFILE.hero_elevation_degrees)
    spin = np.array(
        [
            [math.cos(azimuth), -math.sin(azimuth), 0],
            [math.sin(azimuth), math.cos(azimuth), 0],
            [0, 0, 1],
        ]
    )
    hero_tilt = np.array(
        [
            [1, 0, 0],
            [0, math.cos(elevation), -math.sin(elevation)],
            [0, math.sin(elevation), math.cos(elevation)],
        ]
    )
    rotations.append(hero_tilt @ bases[1] @ spin)
    rows = ",".join(
        "vec4<f32>(" + ",".join(f"{float(x):.9f}" for x in row) + ",0.)"
        for rotation in rotations
        for row in rotation
    )
    return "const rotations = array<vec4<f32>,12>(" + rows + ");"


def frame_config(rotation: Any, width: int, height: int, matte: bool) -> bytes:
    rows = np.zeros((FRAME_STRIDE // 16, 4), dtype="<f4")
    if rotation is not None:
        rows[:3, :3] = rotation
    rows[3] = (width, height, PREVIEW_PROFILE.margin_fraction, rotation is None)
    rows[5, 0] = 0.0 if matte else 0.22
    rows[6, :3] = PREVIEW_PROFILE.material_albedo
    return rows.tobytes()


class ViewPipeline:
    def __init__(self, device: Any):
        self.device = device
        self.bounds = []
        for code in (BOUNDS_VERTICES, BOUNDS_REDUCE):
            shader = device.create_shader_module(code=code)
            self.bounds.append(
                device.create_compute_pipeline(
                    layout="auto", compute={"module": shader, "entry_point": "bounds"}
                )
            )
        shader = device.create_shader_module(
            code=FRAME_SHADER.replace("AUTO_ROTATIONS", _auto_rotations()).replace(
                "FLAT_RATIO", str(PREVIEW_PROFILE.flat_thickness_ratio)
            )
        )
        self.camera = device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "camera"}
        )

    def encode_bounds(
        self, encoder: Any, positions: Any, count: int, target: Any, scratch: list[Any]
    ) -> None:
        source = positions
        for index, result in enumerate([*scratch, target]):
            pipeline = self.bounds[min(index, 1)]
            self._dispatch(encoder, pipeline, [source, result], (count + 63) // 64)
            count = (count + 63) // 64
            source = result

    def _dispatch(self, encoder: Any, pipeline: Any, buffers: list[Any], groups: int):
        group = self.device.create_bind_group(
            layout=pipeline.get_bind_group_layout(0),
            entries=[
                {"binding": i, "resource": {"buffer": buffer}}
                for i, buffer in enumerate(buffers)
            ],
        )
        compute = encoder.begin_compute_pass()
        compute.set_pipeline(pipeline)
        compute.set_bind_group(0, group)
        x = min(groups, self.device.limits["max-compute-workgroups-per-dimension"])
        compute.dispatch_workgroups(x, (groups + x - 1) // x, 1)
        compute.end()

    def encode_camera(
        self, encoder: Any, bounds: Any, configs: Any, frames: Any, index: int
    ) -> None:
        offset = index * FRAME_STRIDE
        group = self.device.create_bind_group(
            layout=self.camera.get_bind_group_layout(0),
            entries=[
                {"binding": 0, "resource": {"buffer": bounds}},
                {
                    "binding": 1,
                    "resource": {
                        "buffer": configs,
                        "offset": offset,
                        "size": FRAME_BYTES,
                    },
                },
                {
                    "binding": 2,
                    "resource": {
                        "buffer": frames,
                        "offset": offset,
                        "size": FRAME_BYTES,
                    },
                },
            ],
        )
        compute = encoder.begin_compute_pass()
        compute.set_pipeline(self.camera)
        compute.set_bind_group(0, group)
        compute.dispatch_workgroups(1)
        compute.end()

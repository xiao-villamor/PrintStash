"""GPU bounds reduction and constant-size camera preparation per view.

The cached model-space bounds select the automatic rotation. Each view then
reduces the rotated positions directly, without a transformed vertex buffer
or a CPU readback.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from printstash_core.mesh.preview_profile import (
    MATTE_LIGHTING,
    PREVIEW_PROFILE,
    REFERENCE_THUMBNAIL_PROFILE,
    STUDIO_LOOKS,
    preview_appearance,
)

from .gpu_render_layout import BOUND_BYTES, FRAME_BYTES, FRAME_STRIDE

FRAME_STRUCT = """
struct Frame {
    r0: vec4<f32>, r1: vec4<f32>, r2: vec4<f32>,
    projection: vec4<f32>, extent: vec4<f32>,
    ambient: f32, specular_strength: f32, specular_power: f32, rim_power: f32,
    albedo: vec3<f32>, studio: f32,
    key_direction: vec4<f32>, fill_direction: vec4<f32>,
    key_colour: vec3<f32>, key_strength: f32,
    fill_colour: vec3<f32>, fill_strength: f32,
    rim_colour: vec3<f32>, rim_strength: f32,
    floor: vec4<f32>,
    footprint: vec4<f32>
};
fn rotate(value: vec3<f32>, frame: Frame) -> vec3<f32> {
    return vec3(dot(value,frame.r0.xyz), dot(value,frame.r1.xyz), dot(value,frame.r2.xyz));
}
"""
BOUND_STRUCT = "struct Bounds { low: vec4<f32>, high: vec4<f32> };"
BOUNDS_COMMON = (
    BOUND_STRUCT
    + """
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
)
BOUNDS_VERTICES = """
FRAME_BINDING
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
        let point = vec3(source[offset], source[offset+1u], source[offset+2u]);
        let value = BOUNDS_POINT;
        lows[local] = value; highs[local] = value;
    }
    reduce(local, group);
}
"""
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
CAMERA_SHADER = (
    FRAME_STRUCT
    + BOUND_STRUCT
    + """
@group(0) @binding(0) var<storage, read> bounds: Bounds;
@group(0) @binding(1) var<uniform> config: Frame;
@group(0) @binding(2) var<storage, read_write> frame: Frame;
AUTO_ROTATIONS
@compute @workgroup_size(1)
fn select_rotation() {
    var r0 = config.r0; var r1 = config.r1; var r2 = config.r2;
    if config.projection.w != 0. {
        let extents = bounds.high.xyz - bounds.low.xyz;
        var axis = 0u;
        if extents.y < extents.x { axis = 1u; }
        if extents.z < extents[axis] { axis = 2u; }
        let broad = min(extents[(axis+1u)%3u], extents[(axis+2u)%3u]);
        var choice = 3u;
        if broad > 1e-6 && extents[axis] / broad <= FLAT_RATIO { choice = axis; }
        if choice < 2u && config.studio != 0. { choice += 4u; }
        r0 = rotations[choice*3u]; r1 = rotations[choice*3u+1u];
        r2 = rotations[choice*3u+2u];
    }
    frame = config;
    frame.r0 = r0; frame.r1 = r1; frame.r2 = r2;
    let extents = bounds.high.xyz - bounds.low.xyz;
    let target_cell = max(extents.x, extents.y)/5.;
    let cells = array<f32, 6>(1., 2., 5., 10., 20., 50.);
    var cell = 50.;
    for (var i = 0u; i < 6u; i++) {
        if target_cell <= cells[i] { cell = cells[i]; break; }
    }
    frame.floor = vec4(bounds.low.z, cell, 0., config.floor.w);
    frame.footprint = vec4(
        (bounds.low.xy + bounds.high.xy)*0.5,
        extents.xy*0.5 + 1.5*cell
    );
}
"""
)
FIT_SHADER = (
    FRAME_STRUCT
    + BOUND_STRUCT
    + """
@group(0) @binding(0) var<storage, read> bounds: Bounds;
@group(0) @binding(1) var<storage, read_write> frame: Frame;
@compute @workgroup_size(1)
fn fit() {
    let low = bounds.low.xyz;
    let high = bounds.high.xyz;
    let extent = max(high-low, vec3(1e-6));
    let size = frame.projection.xy;
    let interior = size * (1. - 2.*frame.projection.z);
    let scale = min(interior.x/extent.x, interior.y/extent.y);
    frame.projection = vec4(low.xy*0.5+high.xy*0.5, scale, 0.);
    frame.extent = vec4(size, low.z, extent.z);
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
    rotations[2] = tilt @ spin @ bases[2]
    hero_tilt = np.array(
        [
            [1, 0, 0],
            [0, math.cos(elevation), -math.sin(elevation)],
            [0, math.sin(elevation), math.cos(elevation)],
        ]
    )
    rotations.append(hero_tilt @ bases[1] @ spin)
    rotations.extend(tilt.T @ base for base in bases[:2])
    rows = ",".join(
        "vec4<f32>(" + ",".join(f"{float(x):.9f}" for x in row) + ",0.)"
        for rotation in rotations
        for row in rotation
    )
    return "const rotations = array<vec4<f32>,18>(" + rows + ");"


def frame_config(
    rotation: Any, width: int, height: int, matte: bool, *, samples: int = 1
) -> bytes:
    rows = np.zeros((FRAME_STRIDE // 16, 4), dtype="<f4")
    if rotation is not None:
        rows[:3, :3] = rotation
    rows[3] = (width, height, PREVIEW_PROFILE.margin_fraction, rotation is None)
    appearance = preview_appearance()
    look = STUDIO_LOOKS[appearance.look]
    lighting = MATTE_LIGHTING if matte else look.lighting
    albedo = REFERENCE_THUMBNAIL_PROFILE.material_albedo if matte else look.albedo
    rows[5] = (
        lighting.ambient,
        lighting.specular_strength,
        lighting.specular_power,
        lighting.rim_power,
    )
    rows[6] = (*albedo, not matte)
    rows[7, :3] = lighting.key.direction
    rows[8, :3] = lighting.fill.direction
    rows[9] = (*lighting.key.colour, lighting.key.strength)
    rows[10] = (*lighting.fill.colour, lighting.fill.strength)
    rows[11] = (*lighting.rim_colour, lighting.rim_strength)
    rows[12, 3] = samples if not matte and appearance.floor_grid else 0
    return rows.tobytes()


class ViewPipeline:
    def __init__(self, device: Any):
        self.device = device
        self.model_bounds = self._pipeline(
            BOUNDS_COMMON
            + BOUNDS_VERTICES.replace("FRAME_BINDING", "").replace(
                "BOUNDS_POINT", "point"
            ),
            "bounds",
        )
        self.view_bounds = self._pipeline(
            FRAME_STRUCT
            + BOUNDS_COMMON
            + BOUNDS_VERTICES.replace(
                "FRAME_BINDING", "@group(0) @binding(2) var<uniform> frame: Frame;"
            ).replace("BOUNDS_POINT", "rotate(point, frame)"),
            "bounds",
        )
        self.reduce_bounds = self._pipeline(BOUNDS_REDUCE, "bounds")
        self.select_rotation = self._pipeline(
            CAMERA_SHADER.replace("AUTO_ROTATIONS", _auto_rotations()).replace(
                "FLAT_RATIO", str(PREVIEW_PROFILE.flat_thickness_ratio)
            ),
            "select_rotation",
        )
        self.fit = self._pipeline(FIT_SHADER, "fit")

    def _pipeline(self, code: str, entry_point: str) -> Any:
        shader = self.device.create_shader_module(code=code)
        return self.device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": entry_point}
        )

    def encode_bounds(
        self,
        encoder: Any,
        positions: Any,
        count: int,
        target: Any,
        scratch: list[Any],
        *,
        frame: dict[str, Any] | None = None,
    ) -> None:
        # The shared view scratch may be larger than this model. Bind only
        # each level's live records so the next pass never reads a stale tail.
        source = {"buffer": positions}
        first = self.model_bounds if frame is None else self.view_bounds
        for index, result in enumerate([*scratch, target]):
            count = (count + 63) // 64
            destination = {"buffer": result, "size": count * BOUND_BYTES}
            if index == 0:
                resources = [source, destination]
                if frame is not None:
                    resources.append(frame)
                self._dispatch(encoder, first, resources, count)
            else:
                self._dispatch(
                    encoder, self.reduce_bounds, [source, destination], count
                )
            source = destination

    def _dispatch(
        self, encoder: Any, pipeline: Any, resources: list[dict[str, Any]], groups: int
    ) -> None:
        group = self.device.create_bind_group(
            layout=pipeline.get_bind_group_layout(0),
            entries=[
                {"binding": i, "resource": resource}
                for i, resource in enumerate(resources)
            ],
        )
        compute = encoder.begin_compute_pass()
        compute.set_pipeline(pipeline)
        compute.set_bind_group(0, group)
        x = min(groups, self.device.limits["max-compute-workgroups-per-dimension"])
        compute.dispatch_workgroups(x, (groups + x - 1) // x, 1)
        compute.end()

    def encode_camera(
        self,
        encoder: Any,
        model_bounds: Any,
        positions: Any,
        vertex_count: int,
        view_bounds: Any,
        scratch: list[Any],
        configs: Any,
        frames: Any,
        index: int,
    ) -> None:
        offset = index * FRAME_STRIDE
        config = {"buffer": configs, "offset": offset, "size": FRAME_BYTES}
        frame = {"buffer": frames, "offset": offset, "size": FRAME_BYTES}
        self._dispatch(
            encoder, self.select_rotation, [{"buffer": model_bounds}, config, frame], 1
        )
        self.encode_bounds(
            encoder, positions, vertex_count, view_bounds, scratch, frame=frame
        )
        self._dispatch(encoder, self.fit, [{"buffer": view_bounds}, frame], 1)

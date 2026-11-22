/** Each slicer's feature names land in the one set the G-code viewer colours. */
import { describe, expect, it } from "vitest";
import { bedCentre, parseGcode } from "../gcode";
import {
  TOOLPATH_FEATURE_COLOURS,
  classifyFeature,
  featureMarker,
  linearRgb,
} from "../gcode-features";

describe("classifyFeature", () => {
  it.each([
    // PrusaSlicer
    ["External perimeter", "outer_wall"],
    ["Perimeter", "inner_wall"],
    ["Overhang perimeter", "overhang_wall"],
    ["Internal infill", "sparse_infill"],
    ["Solid infill", "solid_infill"],
    ["Top solid infill", "top_bottom"],
    ["Bridge infill", "bridge"],
    ["Gap fill", "solid_infill"],
    ["Skirt/Brim", "skirt_brim"],
    ["Support material interface", "support"],
    ["Wipe tower", "other"],
    // OrcaSlicer and Bambu Studio
    ["Outer wall", "outer_wall"],
    ["Inner wall", "inner_wall"],
    ["Overhang wall", "overhang_wall"],
    ["Sparse infill", "sparse_infill"],
    ["Internal solid infill", "solid_infill"],
    ["Top surface", "top_bottom"],
    ["Bottom surface", "top_bottom"],
    ["Ironing", "top_bottom"],
    ["Brim", "skirt_brim"],
    // Cura
    ["WALL-OUTER", "outer_wall"],
    ["WALL-INNER", "inner_wall"],
    ["SKIN", "top_bottom"],
    ["FILL", "sparse_infill"],
    ["SUPPORT", "support"],
    ["SKIRT", "skirt_brim"],
    ["PRIME-TOWER", "other"],
  ])("files %s under %s", (name, feature) => {
    expect(classifyFeature(name)).toBe(feature);
  });
});

describe("featureMarker", () => {
  it("reads only feature markers, in either spelling", () => {
    expect(featureMarker(";TYPE:External perimeter")).toBe("outer_wall");
    expect(featureMarker("; FEATURE: Sparse infill")).toBe("sparse_infill");
    expect(featureMarker(";LAYER:3")).toBeNull();
    expect(featureMarker("G1 X1 ; move")).toBeNull();
  });
});

describe("feature colouring", () => {
  const alpha = (colours: Uint8Array) => colours.filter((_, index) => index % 4 === 3);
  const rgbAt = (colours: Uint8Array, vertex: number) =>
    Array.from(colours.subarray(vertex * 4, vertex * 4 + 3));

  it("colours every extruded vertex by its marked feature", () => {
    const result = parseGcode(
      ["M83", ";TYPE:External perimeter", "G1 X10 E1", ";TYPE:Internal infill", "G1 Y10 E1"].join(
        "\n",
      ),
    );

    expect(result.features).toEqual(["outer_wall", "sparse_infill"]);
    expect(Array.from(result.extrudeFeatures)).toEqual([0, 0, 5, 5]);
    expect(rgbAt(result.extrudeColors, 0)).toEqual(linearRgb(TOOLPATH_FEATURE_COLOURS.outer_wall));
    expect(rgbAt(result.extrudeColors, 3)).toEqual(
      linearRgb(TOOLPATH_FEATURE_COLOURS.sparse_infill),
    );
    expect(alpha(result.extrudeColors).every((value) => value === 255)).toBe(true);
  });

  it("does not count travel under a marked feature", () => {
    const result = parseGcode(["M83", ";TYPE:Support material", "G0 X10", "G1 Y10 E1"].join("\n"));

    expect(result.features).toEqual(["support"]);
    expect(result.extrudeFeatures).toHaveLength(2);
  });

  it("shades unmarked G-code in one hue, lighter towards the top", () => {
    const result = parseGcode(["M83", "G1 Z0.2", "G1 X10 E1", "G1 Z10", "G1 X0 E1"].join("\n"));

    expect(result.features).toEqual([]);
    const bottom = rgbAt(result.extrudeColors, 0);
    const top = rgbAt(result.extrudeColors, 3);
    expect(top.every((channel, index) => channel > bottom[index]!)).toBe(true);
    // One hue: blue stays the strongest channel from bottom to top.
    expect(bottom[2]).toBeGreaterThan(bottom[0]!);
    expect(top[2]).toBeGreaterThan(top[0]!);
  });
});

describe("toolpath placement", () => {
  // An Ender-3 style start: a purge line just off the bed edge, then the object.
  const PURGED = [
    "M83",
    "G1 Z0.2",
    ";TYPE:Custom",
    "G1 X-2 Y20",
    "G1 X-2 Y145 E10",
    ";TYPE:Outer wall",
    "G1 X100 Y100",
    "G1 X120 Y100 E1",
    "G1 X120 Y120 E1",
  ].join("\n");

  it("frames the printed object, not the purge line", () => {
    const { bounds } = parseGcode(PURGED);

    expect(bounds.centerX).toBe(110);
    expect(bounds.centerY).toBe(110);
    expect(bounds.sizeX).toBe(20);
  });

  it("puts a corner-origin bed under the object despite a purge line off its edge", () => {
    const { bounds } = parseGcode(PURGED);

    expect(bedCentre(bounds, { x: 220, y: 220 })).toEqual({ x: 0, z: 0 });
  });

  it("centres a centre-origin bed on the machine origin", () => {
    const { bounds } = parseGcode(["M83", "G1 Z0.2", "G1 X-20 Y-10", "G1 X10 Y-10 E1"].join("\n"));

    // The object's centre is (-5, -10); the bed's centre is the origin.
    expect(bedCentre(bounds, { x: 200, y: 200 })).toEqual({ x: 5, z: -10 });
  });
});

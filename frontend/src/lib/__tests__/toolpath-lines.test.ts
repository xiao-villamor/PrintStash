/** Extrusions draw as print-width tubes, and the legend and layer controls cull them on the GPU. */
import { describe, expect, it } from "vitest";
import { parseGcode } from "@/lib/gcode";
import { createToolpathLines, hiddenFeatureMask, toolpathLineWidth } from "@/lib/toolpath-lines";

const layers = (...heights: number[]) =>
  heights.map((z) => ({ z, vertexStart: 0, vertexCount: 0 }));

describe("toolpathLineWidth", () => {
  it("draws lines twice as wide as the usual layer height", () => {
    expect(toolpathLineWidth(layers(0.2, 0.4, 0.6, 0.8, 1.4))).toBeCloseTo(0.4);
  });

  it("stays within printable widths", () => {
    expect(toolpathLineWidth(layers(0, 0.05, 0.1))).toBe(0.25);
    expect(toolpathLineWidth(layers(0, 1, 2))).toBe(1);
  });

  it("falls back to a common nozzle width for a single layer", () => {
    expect(toolpathLineWidth(layers(0.2))).toBe(0.4);
  });
});

describe("hiddenFeatureMask", () => {
  it("sets one bit per hidden feature in legend order", () => {
    expect(hiddenFeatureMask([])).toBe(0);
    expect(hiddenFeatureMask(["outer_wall", "sparse_infill"])).toBe(0b100001);
  });
});

describe("createToolpathLines", () => {
  const gcode = [
    ";TYPE:External perimeter",
    "G1 X0 Y0 Z0.2 F600",
    "G1 X10 Y0 E1",
    ";TYPE:Internal infill",
    "G1 X10 Y10 E2",
    "G1 Z0.4",
    "G1 X0 Y10 E3",
  ].join("\n");

  it("gives each extrusion segment its own feature, with its colour", () => {
    const data = parseGcode(gcode);
    const { lines, dispose } = createToolpathLines(data);
    const geometry = lines.geometry;
    expect(geometry.getAttribute("instanceStart").count).toBe(3);
    expect(geometry.getAttribute("instanceFeature").count).toBe(3);
    const colour = geometry.getAttribute("instanceColorStart");
    expect(colour.count).toBe(3);
    expect(colour.normalized).toBe(true);
    expect([colour.getX(1), colour.getY(1), colour.getZ(1)]).toEqual(
      Array.from(data.extrudeColors.slice(8, 11), (channel) => channel / 255),
    );
    dispose();
  });

  it("culls hidden features, with out-of-range layers, in the line shader", () => {
    const toolpath = createToolpathLines(parseGcode(gcode));
    const uniforms: Record<string, { value: unknown }> = {};
    const shader = {
      uniforms,
      vertexShader: "void main() {\n}",
      fragmentShader: "gl_FragColor = vec4( diffuseColor.rgb, alpha );",
    };
    // SAFETY: onBeforeCompile reads only the uniforms and shader sources set
    // above, and never the renderer.
    const compile = toolpath.lines.material.onBeforeCompile as (source: typeof shader) => void;
    compile(shader);
    toolpath.showSegments(1, 2);
    toolpath.hideFeatures(["sparse_infill"]);
    expect(shader.vertexShader).toContain("hiddenFeatures >> feature");
    expect(shader.fragmentShader).toContain("tubeNormal");
    expect(shader.uniforms.hiddenFeatures!.value).toBe(0b100000);
    expect(shader.uniforms.segmentRange!.value).toMatchObject({ x: 1, y: 2 });
    toolpath.dispose();
  });
});

/** Geometry reflects machine coordinates, extrusion deltas and circular interpolation. */
import { describe, expect, it } from "vitest";
import { parseGcode } from "../gcode";

describe("Toolpath geometry", () => {
  it("previews arc-heavy builds at every printable layer", () => {
    const layers = Array.from(
      { length: 750 },
      (_, index) => `G1 Z${(index + 1) * 0.2}\nG2 I-100 E1\nG2 I-100 E1`,
    );

    const result = parseGcode(`G1 X100\nM83\n${layers.join("\n")}`);

    expect(result.totalLayers).toBe(750);
    expect(result.layerRanges.every((layer) => layer.vertexCount > 0)).toBe(true);
    expect(result.bounds.sizeX).toBeCloseTo(200, 1);
    expect(result.bounds.sizeY).toBeCloseTo(200, 1);
    expect(result.bounds.sizeZ).toBeCloseTo(149.8);
  });

  it("previews more than one million linear moves", () => {
    const count = 1_000_001;

    const result = parseGcode(`G91\nM83\n${"G1 X.001 E.001\n".repeat(count)}`);

    expect(result.extrudePositions).toHaveLength(count * 6);
    expect(result.totalLayers).toBe(1);
    expect(result.bounds.sizeX).toBeCloseTo(count * 0.001, 4);
    expect(result.cumulativeVertices.at(-1)).toBe(count * 2);
  });

  it.each([16383, 16384, 16385, 32769])(
    "preserves geometry across storage chunks (%s moves)",
    (count) => {
      const result = parseGcode(`G91\nM83\n${"G1 X1 E1\nG1 Y1\n".repeat(count)}G1 Z1\nG1 X1 E1`);

      expect(result.extrudePositions).toHaveLength((count + 1) * 6);
      expect(result.travelPositions).toHaveLength((count + 1) * 6);
      expect(Array.from(result.extrudePositions.filter((_, index) => index % 6 === 0))).toEqual(
        Array.from({ length: count + 1 }, (_, index) => index - (count + 1) / 2),
      );
      expect(Array.from(result.travelPositions.filter((_, index) => index % 6 === 0))).toEqual(
        Array.from(
          { length: count + 1 },
          (_, index) => Math.min(index + 1, count) - (count + 1) / 2,
        ),
      );
      expect(Array.from(result.extrudePositions.slice(-6))).toEqual([
        (count - 1) / 2,
        0.5,
        -count / 2,
        (count + 1) / 2,
        0.5,
        -count / 2,
      ]);
      expect(Array.from(result.travelPositions.slice(-6))).toEqual([
        (count - 1) / 2,
        -0.5,
        -count / 2,
        (count - 1) / 2,
        0.5,
        -count / 2,
      ]);
      expect(result.layerRanges).toEqual([
        { z: 0, vertexStart: 0, vertexCount: count * 2 },
        { z: 1, vertexStart: count * 2, vertexCount: 2 },
      ]);
      expect(Array.from(result.cumulativeVertices)).toEqual([0, count * 2, (count + 1) * 2]);
    },
  );

  it("includes a full-circle extrusion in its printable layer", () => {
    const result = parseGcode("G1 X10 Z.2\nG2 I-10 J0 E1");
    expect(result.layerRanges).toHaveLength(1);
    expect(result.layerRanges[0].z).toBe(0.2);
    expect(result.layerRanges[0].vertexCount).toBe(result.extrudePositions.length / 3);
    expect(result.layerRanges[0].vertexCount).toBeGreaterThan(4);
  });
  it("ignores travel lifts when defining printable layers", () => {
    const result = parseGcode(
      "G1 Z5\nG1 Z.2\nG1 X10 E1\nG1 Z1\nG1 X20\nG1 Z.2\nG1 X30 E2\nG1 Z.4\nG1 X40 E3",
    );
    expect(result.layerRanges.map((layer) => layer.z)).toEqual([0.2, 0.4]);
    expect(result.layerRanges.map((layer) => layer.vertexCount)).toEqual([4, 2]);
    expect(result.cumulativeVertices.at(-1)).toBe(result.extrudePositions.length / 3);
  });
  it("keeps physical coordinates continuous after G92", () => {
    const result = parseGcode("G1 X10 Z.2\nG1 X20 E1\nG92 X0 E0\nG1 X10 E1");
    expect(result.bounds.sizeX).toBe(20);
    const expected = [-10, 0, 0, 0, 0, 0, 0, 0, 0, 10, 0, 0];
    result.extrudePositions.forEach((value, index) =>
      expect(value).toBeCloseTo(expected[index], 6),
    );
  });
  it("uses the relative extrusion delta to classify a retraction", () => {
    const result = parseGcode("G1 X10 E10\nM83\nG1 X20 E-1");
    expect(result.extrudePositions.length).toBe(6);
    expect(result.travelPositions.length).toBe(6);
  });
  it("includes the start of an extrusion in its bounds", () => {
    expect(parseGcode("G1 X20 E1").bounds.sizeX).toBe(20);
  });
  it("parses compact G-code without spaces", () => {
    expect(parseGcode("G1X20Y10E1").bounds.sizeX).toBe(20);
  });
  it("tessellates a clockwise semicircle with relative I/J", () => {
    const result = parseGcode("G1 X10 Z.2\nG2 X-10 Y0 I-10 J0 E1");
    expect(result.extrudePositions.length).toBeGreaterThan(12);
    expect(result.bounds.sizeX).toBeCloseTo(20);
    expect(result.bounds.sizeY).toBeCloseTo(10, 1);
  });
  it("tessellates a counterclockwise radius arc", () => {
    const result = parseGcode("G1 X10 Z.2\nG3 X-10 Y0 R10 E1");
    expect(result.bounds.sizeX).toBeCloseTo(20);
    expect(result.bounds.sizeY).toBeCloseTo(10, 1);
  });
  it("rejects an impossible radius rather than drawing a false line", () => {
    expect(() => parseGcode("G1 X10\nG2 X-10 R1 E1")).toThrow("toolpath_invalid_arc");
  });
  it("enforces a combined segment limit", () => {
    expect(() => parseGcode("G1 X1\nG1 X2 E1", 1)).toThrow("toolpath_segment_limit");
  });
  it("returns physical bounds after switching from relative to absolute positioning", () => {
    const result = parseGcode("G91\nG1 X10 E1\nG1 X5 E2\nG90\nG1 X20 E3");

    expect(result.bounds.sizeX).toBe(20);
  });
  it("classifies retraction after returning to absolute extrusion", () => {
    const result = parseGcode("G1 X10 E10\nM83\nG1 X20 E1\nM82\nG1 X30 E5");

    expect(result.extrudePositions).toHaveLength(12);
    expect(result.travelPositions).toHaveLength(6);
  });
  it("converts inch moves before returning to millimetres", () => {
    const result = parseGcode("G20\nG1 X1 E1\nG21\nG1 X50.8 E30");

    expect(result.bounds.sizeX).toBeCloseTo(50.8);
  });
  it.each([
    { label: "absolute", mode: "G90.1", center: "I0 J0" },
    { label: "relative", mode: "G90.1\nG91.1", center: "I-10 J0" },
  ])("uses $label arc centers", ({ mode, center }) => {
    const result = parseGcode(`G1 X10\n${mode}\nG3 X-10 ${center} E1`);

    expect(result.bounds.sizeX).toBeCloseTo(20);
    expect(result.bounds.sizeY).toBeCloseTo(10, 1);
  });
  it.each([
    { label: "XY", program: "G17\nG1 X10\nG2 X-10 I-10 E1", width: "sizeX", height: "sizeY" },
    { label: "XZ", program: "G18\nG1 X10\nG2 X-10 I-10 E1", width: "sizeX", height: "sizeZ" },
    { label: "YZ", program: "G19\nG1 Y10\nG2 Y-10 J-10 E1", width: "sizeY", height: "sizeZ" },
  ] as const)("interpolates arcs in the $label plane", ({ program, width, height }) => {
    const result = parseGcode(program);

    expect(result.bounds[width]).toBeCloseTo(20);
    expect(result.bounds[height]).toBeCloseTo(10, 1);
  });
  it("resets every logical axis when G92 has no coordinates", () => {
    const result = parseGcode("G1 X10 Y10 Z10 E1\nG92\nG1 X5 Y5 Z5 E1");

    expect(result.bounds).toEqual({ sizeX: 15, sizeY: 15, sizeZ: 15, maxDim: 15 });
  });
  it("rejects coordinates outside finite numeric range", () => {
    expect(() => parseGcode(`G1 X${"9".repeat(400)} E1`)).toThrow("toolpath_invalid_coordinate");
  });
});

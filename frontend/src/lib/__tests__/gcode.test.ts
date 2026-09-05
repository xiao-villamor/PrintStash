/** Geometry reflects machine coordinates, extrusion deltas and circular interpolation. */
import { describe, expect, it } from "vitest";
import { parseGcode } from "../gcode";

describe("Toolpath geometry", () => {
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
});

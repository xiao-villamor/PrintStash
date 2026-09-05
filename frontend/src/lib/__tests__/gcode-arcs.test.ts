/** Circular interpolation preserves plane orientation, helical height and explicit bounds. */
import { describe, expect, it } from "vitest";
import { arcPoints, type ArcOptions } from "../gcode-arcs";

describe("arcPoints", () => {
  it("closes a full circle without losing helical height", () => {
    const points = [
      ...arcPoints([10, 0, 0], [10, 0, 5], {
        plane: "xy",
        center: [0, 0, 0],
        clockwise: false,
        segmentLimit: 1000,
      }),
    ];
    expect(points.at(-1)).toEqual([10, 0, 5]);
    expect(Math.min(...points.map((p) => p[0]))).toBeCloseTo(-10, 1);
    expect(points[Math.floor(points.length / 2)][2]).toBeCloseTo(2.5, 1);
  });
  it("uses the long sweep for a negative radius", () => {
    const options: ArcOptions = {
      plane: "xy",
      center: [0, 0, 0],
      clockwise: false,
      segmentLimit: 1000,
    };
    const short = [...arcPoints([10, 0, 0], [0, 10, 0], { ...options, radius: 10 })];
    const long = [...arcPoints([10, 0, 0], [0, 10, 0], { ...options, radius: -10 })];
    expect(long.length).toBeGreaterThan(short.length * 2);
    expect(long.at(-1)).toEqual([0, 10, 0]);
  });
  it("interpolates G18 orientation in the ZX plane", () => {
    const points = [
      ...arcPoints([0, 7, 10], [0, 7, -10], {
        plane: "xz",
        center: [0, 7, 0],
        clockwise: false,
        segmentLimit: 1000,
      }),
    ];
    expect(Math.max(...points.map((p) => p[0]))).toBeCloseTo(10, 1);
    expect(points.every((p) => p[1] === 7)).toBe(true);
    expect(points.at(-1)).toEqual([0, 7, -10]);
  });
  it("interpolates G19 orientation in the YZ plane", () => {
    const points = [
      ...arcPoints([7, 10, 0], [7, -10, 0], {
        plane: "yz",
        center: [7, 0, 0],
        clockwise: false,
        segmentLimit: 1000,
      }),
    ];
    expect(Math.max(...points.map((p) => p[2]))).toBeCloseTo(10, 1);
    expect(points.every((p) => p[0] === 7)).toBe(true);
  });
  it("refuses a center that cannot reach the endpoint", () => {
    expect(() => [
      ...arcPoints([10, 0, 0], [0, 20, 0], {
        plane: "xy",
        center: [0, 0, 0],
        clockwise: false,
        segmentLimit: 1000,
      }),
    ]).toThrow("toolpath_invalid_arc");
  });
  it("rejects excessive tessellation before yielding partial geometry", () => {
    const points = arcPoints([10, 0, 0], [10, 0, 0], {
      plane: "xy",
      center: [0, 0, 0],
      clockwise: false,
      segmentLimit: 2,
    });
    expect(() => points.next()).toThrow("toolpath_segment_limit");
  });
});

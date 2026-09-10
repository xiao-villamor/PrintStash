/** Comparison uses one physical scale and a camera bus; compensation cannot silently rescale a model. */
import { describe, expect, it } from "vitest";
import { Vector3 } from "three";
import {
  comparisonTransform,
  createComparisonCamera,
  sharedScale,
  type CameraPose,
} from "@/lib/comparison-camera";

describe("sharedScale", () => {
  it("preserves physical size ratios", () => {
    const scale = sharedScale(40);
    expect(20 * scale).toBe(5);
    expect(40 * scale).toBe(10);
  });
  it.each([0, -1, NaN, Infinity])("contains invalid extent %s", (size) => {
    expect(sharedScale(size)).toBe(1);
  });
});
describe("comparisonTransform", () => {
  const alignment = [
    [-2, 0, 0, 80],
    [0, 2, 0, 90],
    [0, 0, 2, 100],
    [0, 0, 0, 1],
  ];
  it.each([
    { label: "neither", compensateScale: false, compensateMirror: false, x: 1, y: 1 },
    { label: "scale", compensateScale: true, compensateMirror: false, x: 2, y: 2 },
    { label: "reflection", compensateScale: false, compensateMirror: true, x: -1, y: 1 },
    { label: "both", compensateScale: true, compensateMirror: true, x: -2, y: 2 },
  ])("applies $label compensation", ({ compensateScale, compensateMirror, x, y }) => {
    const transformed = new Vector3(1, 1, 1).applyMatrix4(
      comparisonTransform({ referenceSizeMm: 10, alignment, compensateScale, compensateMirror }),
    );
    expect(transformed.toArray()).toEqual([x, y, y]);
  });
  it.each([
    undefined,
    [[1]],
    [
      [NaN, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ],
    [
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 1],
    ],
  ])("ignores unusable alignment %j", (input) => {
    expect(
      new Vector3(1, 2, 3)
        .applyMatrix4(comparisonTransform({ referenceSizeMm: 10, alignment: input }))
        .toArray(),
    ).toEqual([1, 2, 3]);
  });
  it("retains the verified rotation without scale compensation", () => {
    const matrix = comparisonTransform({
      referenceSizeMm: 10,
      alignment: [
        [0, -2, 0, 0],
        [2, 0, 0, 0],
        [0, 0, 2, 0],
        [0, 0, 0, 1],
      ],
    });
    expect(new Vector3(1, 0, 0).applyMatrix4(matrix).distanceTo(new Vector3(0, 1, 0))).toBeLessThan(
      1e-12,
    );
  });
});
describe("createComparisonCamera", () => {
  it("delivers the originating camera pose to each subscriber", () => {
    const camera = createComparisonCamera();
    const origin = Symbol("origin");
    const received: CameraPose[] = [];
    camera.subscribe((sender, pose) => {
      if (sender !== origin) received.push(pose);
    });
    camera.subscribe((sender, pose) => {
      if (sender === origin) received.push(pose);
    });
    camera.publish(origin, { position: [3, 4, 5], target: [0, 0, 0] });
    expect(received).toEqual([{ position: [3, 4, 5], target: [0, 0, 0] }]);
  });
  it("stops delivering after unmount", () => {
    const camera = createComparisonCamera();
    const received: CameraPose[] = [];
    const unsubscribe = camera.subscribe((_sender, pose) => received.push(pose));
    unsubscribe();
    camera.publish(Symbol("origin"), { position: [1, 1, 1], target: [0, 0, 0] });
    expect(received).toEqual([]);
  });
});

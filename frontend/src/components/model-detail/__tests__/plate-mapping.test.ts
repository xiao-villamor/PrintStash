import { describe, expect, it } from "vitest";
import { aFilePlate } from "@/test-support/factories";
import { matchSlicedPlate } from "../plate-mapping";

const source = aFilePlate({ number: 2, name: "Wall mount", object_count: 1 });
const sliced = { number: 7, name: "Wall mount", object_count: 1 };

describe("matchSlicedPlate", () => {
  it("matches unique names and counts even when plate numbers differ", () => {
    expect(matchSlicedPlate(source, [source], [sliced])).toEqual(sliced);
  });
  it("does not match different object counts", () => {
    expect(matchSlicedPlate(source, [source], [{ ...sliced, object_count: 2 }])).toBeNull();
  });
  it("does not match duplicate source names", () => {
    expect(matchSlicedPlate(source, [source, { ...source, number: 3 }], [sliced])).toBeNull();
  });
  it("does not match duplicate sliced names", () => {
    expect(matchSlicedPlate(source, [source], [sliced, { ...sliced, number: 8 }])).toBeNull();
  });
  it("does not infer unnamed plates", () => {
    expect(matchSlicedPlate({ ...source, name: null }, [source], [sliced])).toBeNull();
  });
});

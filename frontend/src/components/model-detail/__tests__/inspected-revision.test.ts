/* A removed inspected revision falls back to a real available file, never a stale ID. */
import { describe, expect, it } from "vitest";
import { inspectedRevision } from "../inspected-revision";
import { aRevision } from "@/test-support/factories";

describe("inspectedRevision", () => {
  const recommended = aRevision({ id: 1, is_recommended: true });
  const latest = aRevision({ id: 2, version: 2 });
  it("recovers a removed selection to the recommendation", () => {
    expect(inspectedRevision([recommended, latest], 99)).toBe(recommended);
  });
  it("uses the latest file without a recommendation", () => {
    expect(inspectedRevision([latest], 99)).toBe(latest);
  });
  it("has no selection without files", () => {
    expect(inspectedRevision([], 99)).toBeNull();
  });
});

/** Geometry evidence never infers unchecked topology or treats legacy volume as proof. */
import { describe, expect, it } from "vitest";
import { aDerivative } from "@/test-support/factories";
import { geometryChecks, geometryAnalysis, geometryExplanation } from "@/lib/geometry-checks";
import type { VolumeMeasurement } from "@/types/models";

describe("geometryChecks", () => {
  it("does not infer winding after the open-surface short circuit", () => {
    expect(
      geometryChecks({
        state: "unavailable",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: null,
        cause: "not_watertight",
      }),
    ).toEqual({ watertightness: "warning", winding: "not_checked", volume: "not_checked" });
  });
  it.each<VolumeMeasurement | null>([
    null,
    { state: "legacy_unassessed", unit: "mm3", method: null, value_mm3: 20, cause: null },
    {
      state: "not_calculated",
      unit: "mm3",
      method: null,
      value_mm3: null,
      cause: "geometry_unavailable",
    },
  ])("keeps missing topology evidence unchecked", (volume) => {
    expect(Object.values(geometryChecks(volume))).toEqual([
      "not_checked",
      "not_checked",
      "not_checked",
    ]);
  });
  it("recognizes measured closed mesh evidence", () => {
    expect(
      geometryChecks({
        state: "measured",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: 10,
        cause: null,
      }).volume,
    ).toBe("passed");
  });
  it("reports a failed volume calculation", () => {
    expect(
      geometryChecks({
        state: "unavailable",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: null,
        cause: "measurement_failed",
      }).volume,
    ).toBe("failed");
  });
});

describe("geometryAnalysis", () => {
  it("reports a completed analysis", () => {
    expect(geometryAnalysis(aDerivative({ kind: "metadata", state: "ready" }))).toBe("passed");
  });
  it.each(["invalid_source", "invalid_3mf", "worker_failed", "storage"])(
    "reports failed analysis for %s",
    (reason) => {
      expect(
        geometryAnalysis(
          aDerivative({ kind: "metadata", state: "failed", failure_reason: reason }),
        ),
      ).toBe("failed");
    },
  );
  it.each([
    "unsupported_format",
    "unsupported_capability",
    "unsupported_3mf_capability",
    "resource_limit",
    "scene_resource_limit",
    "timeout",
    "worker_oom",
    "geometry_work_limit",
  ])("keeps %s unchecked", (reason) => {
    expect(
      geometryAnalysis(aDerivative({ kind: "metadata", state: "failed", failure_reason: reason })),
    ).toBe("not_checked");
  });
  it.each(["pending", "queued", "running", "skipped"] as const)(
    "keeps %s analysis unchecked",
    (state) => {
      expect(geometryAnalysis(aDerivative({ kind: "metadata", state }))).toBe("not_checked");
    },
  );
  it("does not use a rendered thumbnail as topology evidence", () => {
    expect(geometryAnalysis(aDerivative())).toBe("not_checked");
  });
});
describe("geometryExplanation", () => {
  it("explains why an open surface stops further checks", () => {
    expect(
      geometryExplanation({
        state: "unavailable",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: null,
        cause: "not_watertight",
      }),
    ).toBe("The surface is open. Winding and volume were not checked.");
  });
  it("explains legacy evidence uncertainty", () => {
    expect(
      geometryExplanation({
        state: "legacy_unassessed",
        unit: "mm3",
        method: null,
        value_mm3: 100,
        cause: null,
      }),
    ).toBe("Older volume data does not include topology checks.");
  });
});

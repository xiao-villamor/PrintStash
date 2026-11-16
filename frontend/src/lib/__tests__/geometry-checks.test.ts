/** Geometry evidence never infers unchecked topology or treats legacy volume as proof. */
import { describe, expect, it } from "vitest";
import { aDerivative } from "@/test-support/factories";
import { geometryChecks, geometryAnalysis, geometryExplanation } from "@/lib/geometry-checks";
import type { VolumeMeasurement } from "@/types/models";

describe("geometryChecks", () => {
  it.each([
    {
      cause: "inconsistent_winding",
      expected: { watertightness: "passed", winding: "warning", volume: "not_checked" },
    },
    {
      cause: "non_positive_integral",
      expected: { watertightness: "passed", winding: "passed", volume: "warning" },
    },
    {
      cause: "nonfinite_integral",
      expected: { watertightness: "passed", winding: "passed", volume: "failed" },
    },
  ] as const)("classifies explicit topology evidence: $cause", ({ cause, expected }) => {
    expect(
      geometryChecks({
        state: "unavailable",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: null,
        cause,
      }),
    ).toEqual(expected);
  });
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
  it("keeps absent analysis unchecked", () => {
    expect(geometryAnalysis(null)).toBe("not_checked");
  });
  it("reports analysis failure without a supplied reason", () => {
    expect(
      geometryAnalysis(aDerivative({ kind: "metadata", state: "failed", failure_reason: null })),
    ).toBe("failed");
  });
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
  it.each([
    {
      cause: "inconsistent_winding",
      expected: "Face orientations are inconsistent. Volume was not measured.",
    },
    { cause: "non_positive_integral", expected: "The calculated volume is not positive." },
    {
      cause: "nonfinite_integral",
      expected: "The volume calculation produced an invalid numeric result.",
    },
    {
      cause: "measurement_failed",
      expected: "Volume calculation failed. Other measurements may still be available.",
    },
  ] as const)("explains unavailable volume evidence: $cause", ({ cause, expected }) => {
    expect(
      geometryExplanation({
        state: "unavailable",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: null,
        cause,
      }),
    ).toBe(expected);
  });
  it.each([
    { cause: "enrichment_pending", expected: "Geometry analysis is pending." },
    { cause: "not_applicable", expected: "Geometry checks do not apply to this file." },
    { cause: "not_requested", expected: "Volume calculation was not requested." },
    { cause: "geometry_unavailable", expected: "Geometry evidence is not available." },
    {
      cause: "topology_not_evaluated",
      expected: "Topology was not evaluated within the analysis limits.",
    },
  ] as const)("explains why geometry was not checked: $cause", ({ cause, expected }) => {
    expect(
      geometryExplanation({
        state: "not_calculated",
        unit: "mm3",
        method: null,
        value_mm3: null,
        cause,
      }),
    ).toBe(expected);
  });
  it.each<VolumeMeasurement | null>([
    null,
    { state: "measured", unit: "mm3", method: "mesh_surface_integral", value_mm3: 10, cause: null },
  ])("omits warnings for measured volume", (volume) => {
    expect(geometryExplanation(volume)).toBeNull();
  });
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

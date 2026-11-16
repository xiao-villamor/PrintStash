import type { MessageKey } from "@/lib/locale";
import type { VolumeMeasurement } from "@/types/models";

export type GeometryCheckState = "passed" | "warning" | "failed" | "not_checked";
export interface GeometryChecks {
  watertightness: GeometryCheckState;
  winding: GeometryCheckState;
  volume: GeometryCheckState;
}
export function geometryChecks(volume: VolumeMeasurement | null): GeometryChecks {
  const unchecked = {
    watertightness: "not_checked",
    winding: "not_checked",
    volume: "not_checked",
  } as const;
  if (!volume || volume.state === "not_calculated" || volume.state === "legacy_unassessed")
    return unchecked;
  if (volume.state === "measured")
    return { watertightness: "passed", winding: "passed", volume: "passed" };
  switch (volume.cause) {
    case "not_watertight":
      return { watertightness: "warning", winding: "not_checked", volume: "not_checked" };
    case "inconsistent_winding":
      return { watertightness: "passed", winding: "warning", volume: "not_checked" };
    case "non_positive_integral":
      return { watertightness: "passed", winding: "passed", volume: "warning" };
    case "nonfinite_integral":
      return { watertightness: "passed", winding: "passed", volume: "failed" };
    case "measurement_failed":
      return { ...unchecked, volume: "failed" };
  }
}

/** A failed producer is not necessarily a failed geometric check. */
export function geometryAnalysis(
  derivative: import("@/types").DerivativeRead | null,
): GeometryCheckState {
  if (!derivative || derivative.kind !== "metadata") return "not_checked";
  if (derivative.state === "ready") return "passed";
  if (derivative.state !== "failed") return "not_checked";
  if (
    [
      "unsupported_format",
      "unsupported_capability",
      "unsupported_3mf_capability",
      "resource_limit",
      "scene_resource_limit",
      "timeout",
      "worker_oom",
      "geometry_work_limit",
    ].includes(derivative.failure_reason ?? "")
  )
    return "not_checked";
  return "failed";
}

export function geometryExplanation(volume: VolumeMeasurement | null): MessageKey | null {
  if (!volume || volume.state === "measured") return null;
  if (volume.state === "legacy_unassessed")
    return "Older volume data does not include topology checks.";
  switch (volume.cause) {
    case "not_watertight":
      return "The surface is open. Winding and volume were not checked.";
    case "inconsistent_winding":
      return "Face orientations are inconsistent. Volume was not measured.";
    case "non_positive_integral":
      return "The calculated volume is not positive.";
    case "nonfinite_integral":
      return "The volume calculation produced an invalid numeric result.";
    case "measurement_failed":
      return "Volume calculation failed. Other measurements may still be available.";
    case "enrichment_pending":
      return "Geometry analysis is pending.";
    case "not_applicable":
      return "Geometry checks do not apply to this file.";
    case "not_requested":
      return "Volume calculation was not requested.";
    case "geometry_unavailable":
      return "Geometry evidence is not available.";
    case "topology_not_evaluated":
      return "Topology was not evaluated within the analysis limits.";
  }
}

import { skipToken, useQuery } from "@tanstack/react-query";
import { getJson } from "@/lib/api/request";
import { derivativeEvidenceKey, useDerivativeEvidenceUpdates } from "./derivative-evidence";
import type { DerivativeState } from "@/types";

export interface ProjectProfile {
  name: string | null;
  printer: string | null;
  materials: string[];
  nozzle_diameters_mm: number[];
  layer_height_mm: number | null;
  wall_loops: number | null;
  infill_percent: number | null;
  plates: { number: number; estimated_seconds: number | null; filament_grams: number | null }[];
}
export interface FileProjectProfile {
  state: DerivativeState;
  profile: ProjectProfile | null;
  failure_reason: string | null;
}

export function useProjectProfile(modelId: number, fileId: number | null) {
  const query = useQuery({
    queryKey: derivativeEvidenceKey(fileId, "project_profile"),
    queryFn:
      fileId === null
        ? skipToken
        : ({ signal }) =>
            getJson<FileProjectProfile>(`/api/v1/files/${fileId}/project-profile`, { signal }),
    retry: false,
  });
  useDerivativeEvidenceUpdates(modelId, fileId, "project_profile");
  return query;
}

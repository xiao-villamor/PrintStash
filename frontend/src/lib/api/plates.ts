import { getJson } from "@/lib/api/request";
import type { FilePlates } from "@/types";

/** A 3MF Artifact's plates, as its `plates` derivative laid them out. */
export function getFilePlates(fileId: number, signal?: AbortSignal): Promise<FilePlates> {
  return getJson<FilePlates>(`/api/v1/files/${fileId}/plates`, { signal });
}

export interface GcodePlateIdentity {
  number: number;
  name: string;
  object_count: number;
}

export type GcodePlateEstimateMetric = "estimated_time_s" | "filament_weight_g" | "filament_cost";

export interface GcodePlateMetadata {
  estimated_time_s: number | null;
  filament_weight_g: number | null;
  filament_cost: number | null;
  material_brand?: string | null;
  material_type?: string | null;
  printer_model?: string | null;
  nozzle_diameter_mm?: number | null;
}

export interface GcodePlates {
  plates: number[];
  identities: GcodePlateIdentity[];
  plate_metadata: { number: number; metadata: GcodePlateMetadata }[];
}

/** Sliced plates need neither source geometry nor a derived mesh layout. */
export function getGcodePlates(fileId: number, signal?: AbortSignal): Promise<GcodePlates> {
  return getJson<GcodePlates>(`/api/v1/files/${fileId}/gcode-plates`, { signal });
}

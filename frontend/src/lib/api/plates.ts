import { getJson } from "@/lib/api/request";
import type { FilePlates } from "@/types";

/** A 3MF Artifact's plates, as its `plates` derivative laid them out. */
export function getFilePlates(fileId: number, signal?: AbortSignal): Promise<FilePlates> {
  return getJson<FilePlates>(`/api/v1/files/${fileId}/plates`, { signal });
}

export interface GcodePlates {
  plates: number[];
}

/** Sliced plates need neither source geometry nor a derived mesh layout. */
export function getGcodePlates(fileId: number, signal?: AbortSignal): Promise<GcodePlates> {
  return getJson<GcodePlates>(`/api/v1/files/${fileId}/gcode-plates`, { signal });
}

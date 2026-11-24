import type { GcodePlates } from "@/lib/api/plates";
import type { FileRead } from "@/types";

/** One compact estimate line, whatever the plate count. */
export type PrintEstimateSummary =
  | { kind: "none" }
  | { kind: "single"; time: number | null; weight: number | null }
  | { kind: "plates"; count: number; time: number; weight: number }
  | { kind: "partial-plates"; count: number };

/**
 * A multi-plate total exists only when every plate carries both estimates;
 * a missing plate never becomes zero.
 */
export function summarizePrintEstimate(
  revision: FileRead,
  estimates: GcodePlates | null,
): PrintEstimateSummary {
  if (estimates !== null && estimates.plates.length > 1) {
    const rows = estimates.plates.map(
      (number) => estimates.plate_metadata.find((entry) => entry.number === number)?.metadata,
    );
    const complete = rows.every(
      (row) => row?.estimated_time_s != null && row.filament_weight_g != null,
    );
    if (!complete) return { kind: "partial-plates", count: estimates.plates.length };
    return {
      kind: "plates",
      count: estimates.plates.length,
      time: rows.reduce((sum, row) => sum + (row?.estimated_time_s ?? 0), 0),
      weight: rows.reduce((sum, row) => sum + (row?.filament_weight_g ?? 0), 0),
    };
  }
  // A 3MF archive's whole-file metadata is not a plate estimate until its plates are read.
  const archive = revision.original_filename.toLowerCase().endsWith(".3mf");
  if (archive && estimates === null) return { kind: "none" };
  const plate = estimates?.plates.length === 1 ? estimates.plate_metadata[0]?.metadata : null;
  const time = plate?.estimated_time_s ?? revision.metadata?.estimated_time_s ?? null;
  const weight = plate?.filament_weight_g ?? revision.metadata?.filament_weight_g ?? null;
  return time === null && weight === null ? { kind: "none" } : { kind: "single", time, weight };
}

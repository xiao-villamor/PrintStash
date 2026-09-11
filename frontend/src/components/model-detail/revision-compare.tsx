"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import {
  formatBytes,
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
} from "@/lib/format";
import { ArtifactOutcomeRead, FileRead } from "@/types";

import { revisionStatusLabel } from "./presentation";
import { MetadataComparison } from "@/components/metadata-comparison";
import { Localized } from "@/components/ui/localized";

export function RevisionCompare({
  left,
  right,
  outcomes = [],
}: {
  left: FileRead;
  right: FileRead;
  outcomes?: ArtifactOutcomeRead[];
}) {
  useUiLocale();
  const leftOutcome = outcomes.find((row) => row.file_id === left.id);
  const rightOutcome = outcomes.find((row) => row.file_id === right.id);
  const leftSlicer =
    [left.metadata?.slicer_name, left.metadata?.slicer_version].filter(Boolean).join(" ") || "—";
  const rightSlicer =
    [right.metadata?.slicer_name, right.metadata?.slicer_version].filter(Boolean).join(" ") || "—";
  const rows: [string, string, string][] = [
    [uiText("Type"), left.file_type.toUpperCase(), right.file_type.toUpperCase()],
    [uiText("Version"), String(left.version), String(right.version)],
    [
      uiText("Status"),
      revisionStatusLabel(left.revision_status),
      revisionStatusLabel(right.revision_status),
    ],
    [
      uiText("Layer height"),
      formatMillimeters(left.metadata?.layer_height_mm),
      formatMillimeters(right.metadata?.layer_height_mm),
    ],
    [
      uiText("First layer"),
      formatMillimeters(left.metadata?.first_layer_height_mm),
      formatMillimeters(right.metadata?.first_layer_height_mm),
    ],
    [
      uiText("Nozzle"),
      formatMillimeters(left.metadata?.nozzle_diameter_mm),
      formatMillimeters(right.metadata?.nozzle_diameter_mm),
    ],
    [
      uiText("Infill"),
      formatPercent(left.metadata?.infill_percent),
      formatPercent(right.metadata?.infill_percent),
    ],
    [
      uiText("Walls"),
      left.metadata?.wall_loops ? String(left.metadata.wall_loops) : "—",
      right.metadata?.wall_loops ? String(right.metadata.wall_loops) : "—",
    ],
    [
      uiText("Top / bottom"),
      left.metadata?.top_shell_layers || left.metadata?.bottom_shell_layers
        ? `${left.metadata?.top_shell_layers ?? "—"} / ${left.metadata?.bottom_shell_layers ?? "—"}`
        : "—",
      right.metadata?.top_shell_layers || right.metadata?.bottom_shell_layers
        ? `${right.metadata?.top_shell_layers ?? "—"} / ${right.metadata?.bottom_shell_layers ?? "—"}`
        : "—",
    ],
    [
      uiText("Supports"),
      left.metadata?.support_material === null || left.metadata?.support_material === undefined
        ? "—"
        : left.metadata.support_material
          ? uiText("Yes")
          : uiText("No"),
      right.metadata?.support_material === null || right.metadata?.support_material === undefined
        ? "—"
        : right.metadata.support_material
          ? uiText("Yes")
          : uiText("No"),
    ],
    [
      "Nozzle temp",
      formatTemperature(left.metadata?.nozzle_temperature_c),
      formatTemperature(right.metadata?.nozzle_temperature_c),
    ],
    [
      "Bed temp",
      formatTemperature(left.metadata?.bed_temperature_c),
      formatTemperature(right.metadata?.bed_temperature_c),
    ],
    [uiText("Material"), left.metadata?.material_type ?? "—", right.metadata?.material_type ?? "—"],
    [
      uiText("Filament profile"),
      left.metadata?.material_brand ?? "—",
      right.metadata?.material_brand ?? "—",
    ],
    [
      uiText("Filament"),
      formatGrams(left.metadata?.filament_weight_g),
      formatGrams(right.metadata?.filament_weight_g),
    ],
    [
      uiText("Filament cost"),
      formatCost(left.metadata?.filament_cost),
      formatCost(right.metadata?.filament_cost),
    ],
    [
      "Est. time",
      formatDuration(left.metadata?.estimated_time_s ?? null),
      formatDuration(right.metadata?.estimated_time_s ?? null),
    ],
    [uiText("Printer"), left.metadata?.printer_model ?? "—", right.metadata?.printer_model ?? "—"],
    [uiText("Slicer"), leftSlicer, rightSlicer],
    [uiText("Size"), formatBytes(left.size_bytes), formatBytes(right.size_bytes)],
    ["SHA-256", left.sha256.slice(0, 12), right.sha256.slice(0, 12)],
    [
      uiText("Prints"),
      String(leftOutcome?.print_count ?? 0),
      String(rightOutcome?.print_count ?? 0),
    ],
    [
      uiText("Completed"),
      String(leftOutcome?.completed_count ?? 0),
      String(rightOutcome?.completed_count ?? 0),
    ],
    [
      uiText("Failed"),
      String(leftOutcome?.failed_count ?? 0),
      String(rightOutcome?.failed_count ?? 0),
    ],
    [
      "Success rate",
      formatPercent(leftOutcome?.success_rate != null ? leftOutcome.success_rate * 100 : null),
      formatPercent(rightOutcome?.success_rate != null ? rightOutcome.success_rate * 100 : null),
    ],
    [
      "Avg actual time",
      formatDuration(leftOutcome?.average_duration_s ?? null),
      formatDuration(rightOutcome?.average_duration_s ?? null),
    ],
    [
      uiText("Actual filament"),
      formatGrams(leftOutcome?.total_filament_g ?? null),
      formatGrams(rightOutcome?.total_filament_g ?? null),
    ],
    [
      uiText("Actual cost"),
      formatCost(leftOutcome?.total_cost ?? null),
      formatCost(rightOutcome?.total_cost ?? null),
    ],
  ];

  return (
    <Localized>
      <MetadataComparison
        headings={[
          `${uiText("Rev ")}${left.gcode_revision_number ?? left.version}`,
          `${uiText("Rev ")}${right.gcode_revision_number ?? right.version}`,
        ]}
        rows={rows}
      />
    </Localized>
  );
}

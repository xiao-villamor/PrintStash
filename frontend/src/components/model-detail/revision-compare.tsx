"use client";

import {
  formatBytes,
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
} from "@/lib/format";
import { FileRead } from "@/types";

import { revisionStatusLabel } from "./presentation";

export function RevisionCompare({ left, right }: { left: FileRead; right: FileRead }) {
  const leftSlicer =
    [left.metadata?.slicer_name, left.metadata?.slicer_version]
      .filter(Boolean)
      .join(" ") || "—";
  const rightSlicer =
    [right.metadata?.slicer_name, right.metadata?.slicer_version]
      .filter(Boolean)
      .join(" ") || "—";
  const rows = [
    [
      "Status",
      revisionStatusLabel(left.revision_status),
      revisionStatusLabel(right.revision_status),
    ],
    [
      "Layer height",
      formatMillimeters(left.metadata?.layer_height_mm),
      formatMillimeters(right.metadata?.layer_height_mm),
    ],
    [
      "First layer",
      formatMillimeters(left.metadata?.first_layer_height_mm),
      formatMillimeters(right.metadata?.first_layer_height_mm),
    ],
    [
      "Nozzle",
      formatMillimeters(left.metadata?.nozzle_diameter_mm),
      formatMillimeters(right.metadata?.nozzle_diameter_mm),
    ],
    [
      "Infill",
      formatPercent(left.metadata?.infill_percent),
      formatPercent(right.metadata?.infill_percent),
    ],
    [
      "Walls",
      left.metadata?.wall_loops ? String(left.metadata.wall_loops) : "—",
      right.metadata?.wall_loops ? String(right.metadata.wall_loops) : "—",
    ],
    [
      "Top / bottom",
      left.metadata?.top_shell_layers || left.metadata?.bottom_shell_layers
        ? `${left.metadata?.top_shell_layers ?? "—"} / ${left.metadata?.bottom_shell_layers ?? "—"}`
        : "—",
      right.metadata?.top_shell_layers || right.metadata?.bottom_shell_layers
        ? `${right.metadata?.top_shell_layers ?? "—"} / ${right.metadata?.bottom_shell_layers ?? "—"}`
        : "—",
    ],
    [
      "Supports",
      left.metadata?.support_material === null || left.metadata?.support_material === undefined
        ? "—"
        : left.metadata.support_material
          ? "Yes"
          : "No",
      right.metadata?.support_material === null || right.metadata?.support_material === undefined
        ? "—"
        : right.metadata.support_material
          ? "Yes"
          : "No",
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
    ["Material", left.metadata?.material_type ?? "—", right.metadata?.material_type ?? "—"],
    ["Filament profile", left.metadata?.material_brand ?? "—", right.metadata?.material_brand ?? "—"],
    [
      "Filament",
      formatGrams(left.metadata?.filament_weight_g),
      formatGrams(right.metadata?.filament_weight_g),
    ],
    [
      "Filament cost",
      formatCost(left.metadata?.filament_cost),
      formatCost(right.metadata?.filament_cost),
    ],
    [
      "Est. time",
      formatDuration(left.metadata?.estimated_time_s ?? null),
      formatDuration(right.metadata?.estimated_time_s ?? null),
    ],
    ["Printer", left.metadata?.printer_model ?? "—", right.metadata?.printer_model ?? "—"],
    ["Slicer", leftSlicer, rightSlicer],
    ["Size", formatBytes(left.size_bytes), formatBytes(right.size_bytes)],
    ["SHA-256", left.sha256.slice(0, 12), right.sha256.slice(0, 12)],
  ];

  return (
    <div className="bg-surface border border-outline-variant rounded overflow-hidden">
      <div className="grid grid-cols-[1fr_1fr_1fr] border-b border-outline-variant bg-surface-container-low">
        <span className="px-2 py-2 font-mono text-3xs uppercase tracking-wider text-on-surface-variant">Field</span>
        <span className="px-2 py-2 font-mono text-3xs uppercase tracking-wider text-on-surface">Rev {left.gcode_revision_number ?? left.version}</span>
        <span className="px-2 py-2 font-mono text-3xs uppercase tracking-wider text-on-surface">Rev {right.gcode_revision_number ?? right.version}</span>
      </div>
      {rows.map(([label, leftValue, rightValue], index) => (
        <div
          key={label}
          className={`grid grid-cols-[1fr_1fr_1fr] ${index === rows.length - 1 ? "" : "border-b border-surface-container-high"}`}
        >
          <span className="px-2 py-2 font-mono text-3xs uppercase tracking-wider text-on-surface-variant">{label}</span>
          <span className="px-2 py-2 font-mono text-2xs text-on-surface break-words">{leftValue}</span>
          <span className={`px-2 py-2 font-mono text-2xs break-words ${leftValue === rightValue ? "text-on-surface" : "text-primary font-semibold"}`}>
            {rightValue}
          </span>
        </div>
      ))}
    </div>
  );
}

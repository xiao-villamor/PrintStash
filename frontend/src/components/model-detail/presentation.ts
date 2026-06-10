/**
 * Presentation maps and derived-row builders shared by the model-detail tabs.
 */

import {
  FileRevisionStatus,
  MetadataRead,
  PrintJobState,
} from "@/types";
import {
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
} from "@/lib/format";
import { MetadataPreferences } from "@/lib/metadata-preferences";

export type TabKey = "overview" | "settings" | "revisions" | "files" | "history";

export const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "settings", label: "Settings" },
  { key: "revisions", label: "Revisions" },
  { key: "files", label: "Files" },
  { key: "history", label: "History" },
];

const REVISION_STATUS_LABELS: Record<FileRevisionStatus, string> = {
  known_good: "Known good",
  needs_test: "Needs test",
  failed: "Failed",
  archived: "Archived",
};

export function revisionStatusClass(status: FileRevisionStatus | null): string {
  switch (status) {
    case "known_good":
      return "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
    case "needs_test":
      return "bg-amber-500/15 text-amber-600 border-amber-500/30";
    case "failed":
      return "bg-[var(--error-container)]/40 text-[var(--error)] border-[var(--error)]/30";
    case "archived":
      return "bg-[var(--surface-container-high)] text-[var(--on-surface-variant)] border-[var(--outline-variant)]";
    default:
      return "bg-[var(--surface-container-low)] text-[var(--on-surface-variant)] border-[var(--outline-variant)]";
  }
}

export function revisionStatusLabel(status: FileRevisionStatus | null): string {
  return status ? REVISION_STATUS_LABELS[status] : "Unmarked";
}

export function headerStatusLabel(status: FileRevisionStatus | null): string {
  return status === "known_good" ? "Printed OK" : revisionStatusLabel(status);
}

export type PrintJobTone = "success" | "error" | "progress";

export const PRINT_JOB_PRESENTATION: Record<
  PrintJobState,
  { label: string; tone: PrintJobTone }
> = {
  queued: { label: "Queued", tone: "progress" },
  uploading: { label: "Uploading", tone: "progress" },
  started: { label: "Started", tone: "progress" },
  printing: { label: "Printing", tone: "progress" },
  paused: { label: "Paused", tone: "progress" },
  completed: { label: "Success", tone: "success" },
  cancelled: { label: "Cancelled", tone: "error" },
  failed: { label: "Failed", tone: "error" },
};

export function printJobToneClass(tone: PrintJobTone): string {
  switch (tone) {
    case "success":
      return "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
    case "error":
      return "bg-[var(--error-container)]/40 text-[var(--error)] border-[var(--error)]/30";
    default:
      return "bg-amber-500/15 text-amber-600 border-amber-500/30";
  }
}

export type PrintSettingRow = {
  label: string;
  value: string;
  chip?: boolean;
  highlight?: boolean;
};

export function buildPrintSettingRows(
  meta: MetadataRead | null | undefined,
  preferences: MetadataPreferences,
): PrintSettingRow[] {
  const rows: PrintSettingRow[] = [];

  if (preferences.printer_profile) {
    rows.push({ label: "PRINTER PROFILE", value: meta?.printer_model ?? "—" });
  }

  if (preferences.material) {
    rows.push({
      label: "MATERIAL",
      value: meta?.material_type ?? "—",
      chip: true,
    });
  }

  if (preferences.filament_profile && meta?.material_brand) {
    rows.push({ label: "FILAMENT PROFILE", value: meta.material_brand });
  }

  if (preferences.layer_height) {
    rows.push({ label: "LAYER HEIGHT", value: formatMillimeters(meta?.layer_height_mm) });
  }

  if (preferences.first_layer && meta?.first_layer_height_mm) {
    rows.push({
      label: "FIRST LAYER",
      value: formatMillimeters(meta.first_layer_height_mm),
    });
  }

  if (preferences.nozzle) {
    rows.push({ label: "NOZZLE", value: formatMillimeters(meta?.nozzle_diameter_mm) });
  }

  if (preferences.infill) {
    rows.push({ label: "INFILL", value: formatPercent(meta?.infill_percent) });
  }

  if (preferences.walls && meta?.wall_loops) {
    rows.push({ label: "WALLS", value: String(meta.wall_loops) });
  }

  if (preferences.top_bottom && (meta?.top_shell_layers || meta?.bottom_shell_layers)) {
    rows.push({
      label: "TOP / BOTTOM",
      value: `${meta?.top_shell_layers ?? "—"} / ${meta?.bottom_shell_layers ?? "—"}`,
    });
  }

  if (
    preferences.supports
    && meta?.support_material !== null
    && meta?.support_material !== undefined
  ) {
    rows.push({ label: "SUPPORTS", value: meta.support_material ? "Yes" : "No" });
  }

  if (preferences.nozzle_temp && meta?.nozzle_temperature_c) {
    rows.push({
      label: "NOZZLE TEMP",
      value: formatTemperature(meta.nozzle_temperature_c),
    });
  }

  if (preferences.bed_temp && meta?.bed_temperature_c) {
    rows.push({ label: "BED TEMP", value: formatTemperature(meta.bed_temperature_c) });
  }

  if (preferences.estimated_time) {
    rows.push({
      label: "EST. TIME",
      value: formatDuration(meta?.estimated_time_s ?? null),
      highlight: true,
    });
  }

  if (preferences.filament_weight) {
    rows.push({ label: "FILAMENT", value: formatGrams(meta?.filament_weight_g) });
  }

  if (preferences.filament_cost && meta?.filament_cost) {
    rows.push({ label: "FILAMENT COST", value: formatCost(meta.filament_cost) });
  }

  return rows;
}

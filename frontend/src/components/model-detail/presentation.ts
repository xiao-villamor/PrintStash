import { uiText, knownUiText } from "@/lib/locale";
/**
 * Presentation maps and derived-row builders shared by the model-detail tabs.
 */

import { FileRead, FileRevisionStatus, MetadataRead, PrintJobState } from "@/types";
import {
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
} from "@/lib/format";
import { MetadataPreferences } from "@/lib/metadata-preferences";

export type TabKey =
  | "overview"
  | "source"
  | "settings"
  | "revisions"
  | "files"
  | "history"
  | "similar";

export const TABS: { key: TabKey; label: string }[] = [
  {
    key: "overview",
    get label() {
      return uiText("Overview");
    },
  },
  {
    key: "source",
    get label() {
      return uiText("Source");
    },
  },
  {
    key: "settings",
    get label() {
      return uiText("Settings");
    },
  },
  {
    key: "revisions",
    get label() {
      return uiText("Revisions");
    },
  },
  {
    key: "files",
    get label() {
      return uiText("Files");
    },
  },
  {
    key: "history",
    get label() {
      return uiText("History");
    },
  },
  {
    key: "similar",
    get label() {
      return uiText("similarity.tab");
    },
  },
];

export function normalizeRecommendedGcodeFiles<
  T extends Pick<FileRead, "id" | "file_type" | "version" | "is_recommended">,
>(files: T[]): T[] {
  const winner = files
    .filter((file) => file.file_type === "gcode" && file.is_recommended)
    .sort((a, b) => b.version - a.version)[0];
  if (!winner) return files;
  return files.map((file) =>
    file.file_type === "gcode" && file.is_recommended && file.id !== winner.id
      ? { ...file, is_recommended: false }
      : file,
  );
}

const REVISION_STATUS_LABELS = {
  known_good: "Known good",
  needs_test: "Needs test",
  failed: "Failed",
  archived: "Archived",
} satisfies Record<FileRevisionStatus, string>;

export function revisionStatusClass(status: FileRevisionStatus | null): string {
  switch (status) {
    case "known_good":
      return "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
    case "needs_test":
      return "bg-amber-500/15 text-amber-600 border-amber-500/30";
    case "failed":
      return "border-destructive/30 bg-destructive/10 text-destructive";
    case "archived":
      return "border-border bg-muted text-muted-foreground";
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

export function revisionStatusLabel(status: FileRevisionStatus | null): string {
  return knownUiText(status ? REVISION_STATUS_LABELS[status] : "Unmarked");
}

export function headerStatusLabel(status: FileRevisionStatus | null): string {
  return status === "known_good" ? uiText("Printed OK") : revisionStatusLabel(status);
}

export type PrintJobTone = "success" | "error" | "progress";

export const PRINT_JOB_PRESENTATION = {
  queued: {
    get label() {
      return uiText("Queued");
    },
    tone: "progress",
  },
  uploading: {
    get label() {
      return uiText("Uploading");
    },
    tone: "progress",
  },
  started: {
    get label() {
      return uiText("Started");
    },
    tone: "progress",
  },
  printing: {
    get label() {
      return uiText("Printing");
    },
    tone: "progress",
  },
  paused: {
    get label() {
      return uiText("Paused");
    },
    tone: "progress",
  },
  completed: {
    get label() {
      return uiText("Success");
    },
    tone: "success",
  },
  cancelled: {
    get label() {
      return uiText("Cancelled");
    },
    tone: "error",
  },
  failed: {
    get label() {
      return uiText("Failed");
    },
    tone: "error",
  },
} satisfies Record<PrintJobState, { label: string; tone: PrintJobTone }>;

export function printJobToneClass(tone: PrintJobTone): string {
  switch (tone) {
    case "success":
      return "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
    case "error":
      return "border-destructive/30 bg-destructive/10 text-destructive";
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
    rows.push({
      get label() {
        return uiText("PRINTER PROFILE");
      },
      value: meta?.printer_model ?? "—",
    });
  }

  if (preferences.material) {
    rows.push({
      get label() {
        return uiText("MATERIAL");
      },
      value: meta?.material_type ?? "—",
      chip: true,
    });
  }

  if (preferences.filament_profile && meta?.material_brand) {
    rows.push({
      get label() {
        return uiText("FILAMENT PROFILE");
      },
      value: meta.material_brand,
    });
  }

  if (preferences.layer_height) {
    rows.push({
      get label() {
        return uiText("LAYER HEIGHT");
      },
      value: formatMillimeters(meta?.layer_height_mm),
    });
  }

  if (preferences.first_layer && meta?.first_layer_height_mm) {
    rows.push({
      get label() {
        return uiText("FIRST LAYER");
      },
      value: formatMillimeters(meta.first_layer_height_mm),
    });
  }

  if (preferences.nozzle) {
    rows.push({
      get label() {
        return uiText("NOZZLE");
      },
      value: formatMillimeters(meta?.nozzle_diameter_mm),
    });
  }

  if (preferences.infill) {
    rows.push({
      get label() {
        return uiText("INFILL");
      },
      value: formatPercent(meta?.infill_percent),
    });
  }

  if (preferences.walls && meta?.wall_loops) {
    rows.push({
      get label() {
        return uiText("WALLS");
      },
      value: String(meta.wall_loops),
    });
  }

  if (preferences.top_bottom && (meta?.top_shell_layers || meta?.bottom_shell_layers)) {
    rows.push({
      get label() {
        return uiText("TOP / BOTTOM");
      },
      value: `${meta?.top_shell_layers ?? "—"} / ${meta?.bottom_shell_layers ?? "—"}`,
    });
  }

  if (
    preferences.supports &&
    meta?.support_material !== null &&
    meta?.support_material !== undefined
  ) {
    rows.push({
      get label() {
        return uiText("SUPPORTS");
      },
      value: meta.support_material ? uiText("Yes") : uiText("No"),
    });
  }

  if (preferences.nozzle_temp && meta?.nozzle_temperature_c) {
    rows.push({
      get label() {
        return uiText("NOZZLE TEMP");
      },
      value: formatTemperature(meta.nozzle_temperature_c),
    });
  }

  if (preferences.bed_temp && meta?.bed_temperature_c) {
    rows.push({
      get label() {
        return uiText("BED TEMP");
      },
      value: formatTemperature(meta.bed_temperature_c),
    });
  }

  if (preferences.estimated_time) {
    rows.push({
      get label() {
        return uiText("EST. TIME");
      },
      value: formatDuration(meta?.estimated_time_s ?? null),
      highlight: true,
    });
  }

  if (preferences.filament_weight) {
    rows.push({
      get label() {
        return uiText("FILAMENT");
      },
      value: formatGrams(meta?.filament_weight_g),
    });
  }

  if (preferences.filament_cost && meta?.filament_cost) {
    rows.push({
      get label() {
        return uiText("FILAMENT COST");
      },
      value: formatCost(meta.filament_cost),
    });
  }

  return rows;
}

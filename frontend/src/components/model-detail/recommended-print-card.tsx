"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { Download, GitCompare, Loader2, Plus, Send, Star, XCircle } from "lucide-react";

import { downloadAuthenticatedFile } from "@/lib/api";
import { toast } from "@/lib/toast";
import { formatDuration, formatGrams, formatMillimeters } from "@/lib/format";
import { FileRead, FileRevisionUpdate } from "@/types";

import { PrintSettingRow, headerStatusLabel, revisionStatusClass } from "./presentation";
import { SettingRow } from "./setting-row";
import { Localized } from "@/components/ui/localized";

export function RecommendedPrintCard({
  file,
  hasGcode,
  saving,
  onSend,
  canSend,
  onCompare,
  onMark,
  onAddRevision,
}: {
  file: FileRead | null;
  hasGcode: boolean;
  saving: number | null;
  onSend: (fileId: number) => void;
  canSend: boolean;
  onCompare: () => void;
  onMark: (file: FileRead, patch: FileRevisionUpdate) => void;
  onAddRevision: () => void;
}) {
  useUiLocale();
  const meta = file?.metadata;
  const isSaving = file ? saving === file.id : false;

  const rows: PrintSettingRow[] = file
    ? [
        {
          get label() {
            return uiText("PRINTER");
          },
          value: meta?.printer_model ?? "—",
        },
        {
          get label() {
            return uiText("MATERIAL");
          },
          value: meta?.material_type ?? "—",
          chip: true,
        },
        {
          get label() {
            return uiText("LAYER HEIGHT");
          },
          value: formatMillimeters(meta?.layer_height_mm),
        },
        {
          get label() {
            return uiText("EST. TIME");
          },
          value: formatDuration(meta?.estimated_time_s ?? null),
          highlight: true,
        },
        {
          get label() {
            return uiText("FILAMENT");
          },
          value: formatGrams(meta?.filament_weight_g),
        },
        {
          get label() {
            return uiText("SLICER");
          },
          value: [meta?.slicer_name, meta?.slicer_version].filter(Boolean).join(" ") || "—",
        },
      ]
    : [];

  return (
    <Localized>
      <section>
        <h2 className="text-lg font-semibold text-on-surface mb-4 pb-1 border-b border-outline-variant flex items-center gap-2">
          <Star className="h-4 w-4 text-primary" />
          {uiText(" Recommended Print")}
        </h2>

        {!file ? (
          <div className="rounded border border-outline-variant bg-surface p-4 space-y-3">
            <p className="font-mono text-xs text-on-surface-variant leading-relaxed">
              {hasGcode
                ? uiText(
                    "No revision is marked as recommended yet. Mark a known-good G-code as recommended.",
                  )
                : uiText(
                    "No sliced G-code yet. Add a revision to capture the settings that worked.",
                  )}
            </p>
            <button
              onClick={onAddRevision}
              className="w-full py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-xs uppercase tracking-wider hover:bg-surface-container-low transition-colors flex items-center justify-center gap-1.5"
            >
              <Plus className="h-4 w-4" />
              {uiText(" Add G-code revision")}
            </button>
          </div>
        ) : (
          <div className="rounded border border-primary/30 bg-primary-fixed/15 p-3 space-y-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-2xs text-primary font-bold uppercase tracking-wider">
                {uiText("Rev ")}
                {file.gcode_revision_number ?? file.version}
              </span>
              <span
                className={`border rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider ${revisionStatusClass(file.revision_status)}`}
              >
                {headerStatusLabel(file.revision_status)}
              </span>
              {file.is_recommended && (
                <span className="inline-flex items-center gap-1 border border-primary/30 bg-secondary-container text-on-secondary-container rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider">
                  <Star className="h-3 w-3 fill-current" />
                  {uiText(" Recommended")}
                </span>
              )}
            </div>
            <p className="text-sm text-on-surface font-medium truncate">{file.original_filename}</p>
            <div className="bg-surface border border-outline-variant rounded flex flex-col">
              {rows.map((row, index) => (
                <SettingRow
                  key={row.label}
                  label={row.label}
                  value={row.value}
                  chip={row.chip}
                  highlight={row.highlight}
                  last={index === rows.length - 1}
                />
              ))}
            </div>

            {canSend && (
              <button
                onClick={() => onSend(file.id)}
                className="w-full py-2.5 bg-primary text-primary-foreground hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2"
              >
                <Send className="h-4 w-4" />
                {uiText(" Send to printer")}
              </button>
            )}
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() =>
                  downloadAuthenticatedFile(
                    `/api/v1/files/${file.id}/download`,
                    file.original_filename,
                  ).catch((e) => toast.error(e))
                }
                className="py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-2xs uppercase tracking-wider hover:bg-surface-container-low transition-colors flex items-center justify-center gap-1.5"
              >
                <Download className="h-4 w-4" />
                {uiText(" Download")}
              </button>
              <button
                onClick={onCompare}
                className="py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-2xs uppercase tracking-wider hover:bg-surface-container-low transition-colors flex items-center justify-center gap-1.5"
              >
                <GitCompare className="h-4 w-4" />
                {uiText(" Compare")}
              </button>
              <button
                onClick={() => onMark(file, { revision_status: "failed" })}
                disabled={isSaving}
                className="py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-2xs uppercase tracking-wider hover:bg-surface-container-low transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
              >
                <XCircle className="h-4 w-4" />
                {uiText(" Mark failed")}
              </button>
              <button
                onClick={() =>
                  onMark(file, { is_recommended: true, revision_status: "known_good" })
                }
                disabled={isSaving || file.is_recommended}
                className="py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-2xs uppercase tracking-wider hover:bg-surface-container-low transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSaving ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Star className="h-4 w-4" />
                )}{" "}
                {uiText("Recommend")}
              </button>
            </div>
          </div>
        )}
      </section>
    </Localized>
  );
}

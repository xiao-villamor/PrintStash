"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { formatPlateEstimate } from "./format-plate-estimate";
import type { GcodePlateMetadata } from "@/lib/api/plates";
import { useFilePlates, useGcodePlates } from "@/lib/queries/file-plates";
import { HashValue } from "./file-hashes";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import {
  formatBytes,
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatPercent,
  formatTemperature,
  formatNumber,
} from "@/lib/format";
import { uiText, currentLocale } from "@/lib/locale";
import { useUiLocale, type MessageKey } from "@/lib/i18n";
import type { ArtifactOutcomeRead, FileRead } from "@/types";
import { slicerDisplayName, revisionStatusLabel } from "./presentation";

export type ComparisonMode = "gcode" | "source" | "all";
type Value = string | null | { number: number; name?: string; value: string }[];
interface ComparisonRow {
  labelKey: MessageKey | "SHA-256";
  left: Value;
  right: Value;
}
interface ComparisonGroup {
  titleKey: MessageKey;
  rows: ComparisonRow[];
}

function title(file: FileRead): string {
  return file.file_type === "gcode"
    ? `${uiText("Rev ")}${file.gcode_revision_number ?? file.version}`
    : `${file.file_type.toUpperCase()} v${file.version}`;
}

function ComparisonTable({
  group,
  compact,
  left,
  right,
}: {
  compact: boolean;
  group: ComparisonGroup;
  left: FileRead;
  right: FileRead;
}) {
  const columns = (["left", "right"] as const).filter(
    (side) => !compact || group.rows.some((row) => row[side] !== null),
  );
  if (!columns.length) return null;
  const files = { left, right };
  const rows = compact
    ? group.rows
        .filter((row) => row.left !== null || row.right !== null)
        .sort(
          (a, b) =>
            Number(b.left !== null && b.right !== null) -
            Number(a.left !== null && a.right !== null),
        )
    : group.rows;
  return (
    <section className="min-w-0 space-y-2.5">
      <h3 className="text-sm font-semibold">{uiText(group.titleKey)}</h3>
      <table className="w-full table-fixed text-left text-xs sm:text-sm">
        <thead className="bg-muted/40">
          <tr>
            <th scope="col" className="w-2/5 px-2 py-2 font-medium">
              {uiText("Field")}
            </th>
            {columns.map((side) => (
              <th key={side} scope="col" className="break-words px-2 py-2 font-medium">
                {title(files[side])}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const different =
              row.left !== null &&
              !Array.isArray(row.left) &&
              row.right !== null &&
              !Array.isArray(row.right) &&
              row.left !== row.right;
            return (
              <tr
                key={row.labelKey}
                className={`border-b border-border ${different ? "bg-accent/60" : ""}`}
              >
                <th
                  scope="row"
                  className="break-words px-2 py-2 align-top font-normal text-muted-foreground"
                >
                  {row.labelKey === "SHA-256" ? row.labelKey : uiText(row.labelKey)}
                </th>
                {columns.map((side) => {
                  const value = row[side];
                  return (
                    <td
                      key={side}
                      className="break-words px-2 py-2 align-top tabular-nums [overflow-wrap:anywhere]"
                    >
                      {Array.isArray(value) ? (
                        <dl className="space-y-2">
                          {value.map((plate) => (
                            <div key={plate.number}>
                              <dt className="text-xs text-muted-foreground" title={plate.name}>
                                {uiText("Plate {value1}", {
                                  value1: String(plate.number).padStart(2, "0"),
                                })}
                              </dt>
                              <dd>{plate.value}</dd>
                            </div>
                          ))}
                        </dl>
                      ) : value &&
                        (row.labelKey === "SHA-256" ||
                          row.labelKey === "File SHA-256" ||
                          row.labelKey === "Geometry SHA-256") ? (
                        <HashValue
                          sha={value}
                          label={row.labelKey === "SHA-256" ? row.labelKey : uiText(row.labelKey)}
                          filename={files[side].original_filename}
                        />
                      ) : different ? (
                        <mark className="bg-transparent text-accent-foreground">{value}</mark>
                      ) : (
                        <span className={value === null ? "text-muted-foreground" : ""}>
                          {value ?? uiText("Not recorded")}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

/** Compare only recorded values; an absent outcome or metadata value never becomes zero. */
export function RevisionCompare({
  left,
  right,
  outcomes = [],
  files = [left, right],
  onSelectLeft,
  onSelectRight,
  onClose,
  backLabel = "Back to revisions",
  initialMode,
  mode: controlledMode,
  onModeChange,
}: {
  left: FileRead;
  right: FileRead;
  outcomes?: ArtifactOutcomeRead[];
  files?: FileRead[];
  onSelectLeft?: (id: number) => void;
  onSelectRight?: (id: number) => void;
  onClose?: () => void;
  backLabel?: MessageKey;
  initialMode?: ComparisonMode;
  mode?: ComparisonMode;
  onModeChange?: (mode: ComparisonMode) => void;
}) {
  useUiLocale();
  const [showAllFields, setShowAllFields] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);
  const [localMode, setLocalMode] = useState<ComparisonMode>(
    initialMode ??
      (left.file_type === "gcode" && right.file_type === "gcode"
        ? "gcode"
        : left.file_type !== "gcode" && right.file_type !== "gcode"
          ? "source"
          : "all"),
  );
  const mode = controlledMode ?? localMode;
  function candidates(kind: ComparisonMode) {
    return files.filter(
      (file) => kind === "all" || (file.file_type === "gcode") === (kind === "gcode"),
    );
  }
  const choices = candidates(mode);
  function changeMode(kind: ComparisonMode) {
    const next = candidates(kind);
    const first = next.find((file) => file.id === left.id) ?? next[0];
    const second =
      next.find((file) => file.id === right.id && file.id !== first?.id) ??
      next.find((file) => file.id !== first?.id);
    setLocalMode(kind);
    onModeChange?.(kind);
    if (first) onSelectLeft?.(first.id);
    if (second) onSelectRight?.(second.id);
  }
  // A mode change must render values from that scope immediately, including
  // an initial selection restored from a different scope or a removed file.
  const selectedLeft = choices.find((file) => file.id === left.id) ?? choices[0] ?? left;
  const selectedRight =
    choices.find((file) => file.id === right.id && file.id !== selectedLeft.id) ??
    choices.find((file) => file.id !== selectedLeft.id) ??
    right;
  const { user, loading } = useAuth();
  const canReadPlates = !loading && user !== null && mode === "source" && choices.length >= 2;
  const leftPlates = useFilePlates(
    selectedLeft.model_id,
    canReadPlates && selectedLeft.file_type === "3mf" ? selectedLeft.id : null,
  );
  const rightPlates = useFilePlates(
    selectedRight.model_id,
    canReadPlates && selectedRight.file_type === "3mf" ? selectedRight.id : null,
  );
  const slicedId = (file: FileRead) =>
    !loading &&
    user &&
    choices.length >= 2 &&
    file.file_type === "gcode" &&
    file.original_filename.toLowerCase().endsWith(".3mf")
      ? file.id
      : null;
  const mixedSourceAndRevision =
    (selectedLeft.file_type === "gcode") !== (selectedRight.file_type === "gcode");
  const leftSliced = useGcodePlates(selectedLeft.model_id, slicedId(selectedLeft));
  const rightSliced = useGcodePlates(selectedRight.model_id, slicedId(selectedRight));
  function estimate(
    file: FileRead,
    metric: keyof GcodePlateMetadata,
    format: (value: number) => string,
  ): Value {
    const data = file.id === selectedLeft.id ? leftSliced : rightSliced;
    if (
      data &&
      data.plates.length > 1 &&
      data.plate_metadata?.some((plate) => plate.metadata[metric] != null)
    ) {
      return data.plates.map((number) => ({
        number,
        name: data.identities?.find((plate) => plate.number === number)?.name,
        value: formatPlateEstimate(
          metric,
          data.plate_metadata.find((plate) => plate.number === number)?.metadata[metric],
        ),
      }));
    }
    const value = file.metadata?.[metric];
    return value == null ? null : format(value);
  }
  const leftOutcome = outcomes.find((row) => row.file_id === selectedLeft.id);
  const rightOutcome = outcomes.find((row) => row.file_id === selectedRight.id);
  const numeric = (
    value: number | null | undefined,
    format: (value: number) => string = String,
  ): Value => (value == null ? null : format(value));
  const string = (value: string | null | undefined): Value =>
    value == null || value === "" ? null : value;
  const row = (
    labelKey: ComparisonRow["labelKey"],
    read: (file: FileRead, outcome: ArtifactOutcomeRead | undefined) => Value,
  ): ComparisonRow => ({
    labelKey,
    left: read(selectedLeft, leftOutcome),
    right: read(selectedRight, rightOutcome),
  });
  const emptyHistory =
    mode !== "source" && leftOutcome?.print_count === 0 && rightOutcome?.print_count === 0;
  const summarizeHistory = emptyHistory && !showAllFields;
  const metadata = [
    row("Type", (file) => file.file_type.toUpperCase()),
    row("Version", (file) => String(file.version)),
    row("File size", (file) => formatBytes(file.size_bytes)),
    row("File SHA-256", (file) => file.sha256),
    row("Geometry SHA-256", (file) => file.geometric_sha256),
    row("Plates", (file) => {
      const data = file.id === selectedLeft.id ? leftPlates : rightPlates;
      return data?.state === "ready" ? String(data.plates.length) : null;
    }),
    row("Added", (file) => new Date(file.uploaded_at).toLocaleDateString(currentLocale())),
  ];
  const groups: ComparisonGroup[] =
    mode === "source"
      ? [
          {
            titleKey: "Source file metadata",
            rows: metadata.map((entry) => ({
              ...entry,
              labelKey:
                entry.labelKey === "Type"
                  ? "Format"
                  : entry.labelKey === "File size"
                    ? "Size"
                    : entry.labelKey,
            })),
          },
        ]
      : [
          {
            titleKey: "Printer results & file history",
            rows: [
              ...(summarizeHistory
                ? []
                : [
                    row("Recorded printer results", (_file, outcome) =>
                      outcome
                        ? `${uiText("Completed")}: ${outcome.completed_count} · ${uiText("Failed")}: ${outcome.failed_count}`
                        : null,
                    ),
                  ]),
              row("Manual label", (file) =>
                file.revision_status === null ? null : revisionStatusLabel(file.revision_status),
              ),
              ...(summarizeHistory
                ? []
                : [
                    row("Print count", (_file, outcome) => numeric(outcome?.print_count)),
                    row("Completed", (_file, outcome) => numeric(outcome?.completed_count)),
                    row("Failed", (_file, outcome) => numeric(outcome?.failed_count)),
                    row("Success rate · all printers", (_file, outcome) =>
                      outcome?.success_rate == null
                        ? null
                        : formatPercent(outcome.success_rate * 100),
                    ),
                  ]),
              ...metadata.filter(
                (entry) =>
                  entry.labelKey !== "Version" &&
                  entry.labelKey !== "Plates" &&
                  entry.labelKey !== "Added",
              ),
              row("Revision / version", (file) =>
                String(file.gcode_revision_number ?? file.version),
              ),
              row("Average actual duration", (_file, outcome) =>
                numeric(outcome?.average_duration_s, formatDuration),
              ),
              row("Measured filament", (_file, outcome) =>
                numeric(outcome?.total_filament_g, formatGrams),
              ),
              row("Actual cost", (_file, outcome) => numeric(outcome?.total_cost, formatCost)),
            ],
          },
          {
            titleKey: "Print settings",
            rows: [
              row("Printer profile", (file) => string(file.metadata?.printer_model)),
              row("Material", (file) => string(file.metadata?.material_type)),
              row("Filament profile", (file) => string(file.metadata?.material_brand)),
              row("Layer height", (file) =>
                numeric(file.metadata?.layer_height_mm, formatMillimeters),
              ),
              row("First layer", (file) =>
                numeric(file.metadata?.first_layer_height_mm, formatMillimeters),
              ),
              row("Nozzle", (file) =>
                numeric(file.metadata?.nozzle_diameter_mm, formatMillimeters),
              ),
              row("Infill", (file) => numeric(file.metadata?.infill_percent, formatPercent)),
              row("Walls", (file) => numeric(file.metadata?.wall_loops)),
              row("Top / bottom", (file) =>
                file.metadata?.top_shell_layers == null &&
                file.metadata?.bottom_shell_layers == null
                  ? null
                  : `${file.metadata?.top_shell_layers ?? uiText("Not recorded")} / ${file.metadata?.bottom_shell_layers ?? uiText("Not recorded")}`,
              ),
              row("Supports", (file) =>
                file.metadata?.support_material == null
                  ? null
                  : uiText(file.metadata.support_material ? "Yes" : "No"),
              ),
              row("Nozzle temperature", (file) =>
                numeric(file.metadata?.nozzle_temperature_c, formatTemperature),
              ),
              row("Bed temperature", (file) =>
                numeric(file.metadata?.bed_temperature_c, formatTemperature),
              ),
              row("Estimated time", (file) =>
                estimate(file, "estimated_time_s", (value) =>
                  formatPlateEstimate("estimated_time_s", value),
                ),
              ),
              row("Filament weight", (file) =>
                estimate(file, "filament_weight_g", (value) =>
                  formatPlateEstimate("filament_weight_g", value),
                ),
              ),
              row("Filament cost", (file) => estimate(file, "filament_cost", formatCost)),
              row("LAYERS", (file) => numeric(file.metadata?.layer_count, formatNumber)),
              ...(selectedLeft.file_type === "resin" || selectedRight.file_type === "resin"
                ? [
                    row("EXPOSURE", (file) =>
                      numeric(
                        file.metadata?.exposure_time_s,
                        (value) => `${formatNumber(value)} s`,
                      ),
                    ),
                    row("RESIN VOLUME", (file) =>
                      numeric(
                        file.metadata?.resin_volume_ml,
                        (value) => `${formatNumber(value)} ml`,
                      ),
                    ),
                  ]
                : []),
              row("Slicer / version", (file) => slicerDisplayName(file.metadata)),
            ],
          },
        ];
  const heading: MessageKey =
    mode === "source"
      ? "Compare source files"
      : mode === "all"
        ? "Compare files"
        : "Compare revisions";
  return (
    <Localized>
      <section className="@container/comparison space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 ref={headingRef} tabIndex={-1} className="text-base font-semibold">
            {uiText(heading)}
          </h2>
          <div role="group" aria-label={uiText("Comparison type")} className="flex flex-wrap gap-1">
            {(
              [
                { mode: "gcode", labelKey: "G-code revisions" },
                { mode: "source", labelKey: "Source files" },
                { mode: "all", labelKey: "All files" },
              ] as const
            ).map((kind) => (
              <Button
                key={kind.mode}
                variant="ghost"
                size="xs"
                aria-pressed={mode === kind.mode}
                className={mode === kind.mode ? "bg-accent text-accent-foreground" : ""}
                onClick={() => changeMode(kind.mode)}
              >
                {uiText(kind.labelKey)}
              </Button>
            ))}
          </div>
          {onClose && (
            <Button size="xs" variant="ghost" onClick={onClose}>
              {uiText(backLabel)}
            </Button>
          )}
        </div>
        {choices.length < 2 ? (
          <p className="text-sm text-muted-foreground">
            {uiText("Add a second file to compare recorded information.")}
          </p>
        ) : (
          <>
            <div className="grid gap-4 @lg/comparison:grid-cols-2">
              {(
                [
                  { labelKey: "Inspecting", selected: selectedLeft, select: onSelectLeft },
                  { labelKey: "Compare with", selected: selectedRight, select: onSelectRight },
                ] as const
              ).map((side) => (
                <fieldset key={side.labelKey} className="min-w-0">
                  <legend className="mb-1.5 text-xs text-muted-foreground">
                    {uiText(side.labelKey)}
                  </legend>
                  <div className="flex flex-wrap gap-1">
                    {choices.map((file) => (
                      <Button
                        key={file.id}
                        size="xs"
                        variant="ghost"
                        aria-pressed={side.selected.id === file.id}
                        disabled={!side.select}
                        className={
                          side.selected.id === file.id ? "bg-accent text-accent-foreground" : ""
                        }
                        onClick={() => side.select?.(file.id)}
                      >
                        {title(file)}
                        {file.is_recommended ? ` · ${uiText("Recommended")}` : ""}
                      </Button>
                    ))}
                  </div>
                </fieldset>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-4 text-xs text-muted-foreground">
              {[selectedLeft, selectedRight].map((file, index) => (
                <p key={index} className="min-w-0 break-words">
                  <strong className="mr-1.5 font-medium text-foreground">
                    {title(file)}
                    {file.revision_label ? ` · ${file.revision_label}` : ""}
                  </strong>
                  {file.original_filename}
                </p>
              ))}
            </div>
            {(mixedSourceAndRevision || emptyHistory) && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={showAllFields}
                  onChange={(event) => setShowAllFields(event.target.checked)}
                />
                {uiText("Show all comparison fields")}
              </label>
            )}
            {summarizeHistory && (
              <p className="text-sm text-muted-foreground">
                {uiText("Neither file has recorded prints.")}
              </p>
            )}
            <div className={`grid gap-5 ${mode === "source" ? "" : "@2xl/comparison:grid-cols-2"}`}>
              {groups.map((group) => (
                <ComparisonTable
                  key={group.titleKey}
                  group={group}
                  compact={!showAllFields && mixedSourceAndRevision}
                  left={selectedLeft}
                  right={selectedRight}
                />
              ))}
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {uiText("Highlights mark changed recorded values, not missing data.")}
            </p>
          </>
        )}
      </section>
    </Localized>
  );
}

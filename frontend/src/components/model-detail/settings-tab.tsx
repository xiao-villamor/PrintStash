"use client";

import type { ReactNode } from "react";
import {
  formatCost,
  formatDuration,
  formatGrams,
  formatMillimeters,
  formatNumber,
  formatPercent,
  formatTemperature,
} from "@/lib/format";
import { uiText } from "@/lib/locale";
import { useUiLocale, type MessageKey } from "@/lib/i18n";
import type { MetadataPreferences } from "@/lib/metadata-preferences";
import type { MetadataRead } from "@/types";
import type { PrintSettingRow } from "./presentation";
import { Localized } from "@/components/ui/localized";

interface SettingValue {
  labelKey: MessageKey;
  value: string | null;
  visible: boolean;
}
interface SettingGroup {
  titleKey: MessageKey;
  rows: SettingValue[];
}

function Group({ group }: { group: SettingGroup }) {
  const rows = group.rows.filter((row) => row.visible);
  if (rows.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{uiText(group.titleKey)}</h3>
      <dl className="text-sm">
        {rows.map((row) => (
          <div
            key={row.labelKey}
            className="grid grid-cols-[minmax(0,.9fr)_minmax(0,1.1fr)] items-baseline gap-4 border-b border-border px-2.5 py-2 odd:bg-muted/40"
          >
            <dt className="min-w-0 text-muted-foreground">{uiText(row.labelKey)}</dt>
            <dd
              className={`min-w-0 break-words tabular-nums ${row.value === null ? "text-muted-foreground" : "text-foreground"}`}
            >
              {row.value ?? uiText("Not recorded")}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Recorded settings retain zero/false values; display preferences only hide fields. */
export function SettingsTab({
  meta,
  preferences,
  headerActions,
  revisionPicker,
  actions,
}: {
  meta: MetadataRead | null | undefined;
  printSettingRows: PrintSettingRow[];
  preferences: MetadataPreferences;
  headerActions?: ReactNode;
  revisionPicker?: ReactNode;
  actions?: ReactNode;
}) {
  useUiLocale();
  const hasMeshGeometry = meta?.volume_mm3 != null || meta?.triangle_count != null;
  const hasEmbeddedSlicerMetadata = Boolean(
    meta &&
    [
      meta.slicer_name,
      meta.slicer_version,
      meta.printer_model,
      meta.nozzle_diameter_mm,
      meta.layer_height_mm,
      meta.first_layer_height_mm,
      meta.material_type,
      meta.material_brand,
      meta.infill_percent,
      meta.wall_loops,
      meta.top_shell_layers,
      meta.bottom_shell_layers,
      meta.support_material,
      meta.nozzle_temperature_c,
      meta.bed_temperature_c,
      meta.layer_count,
      meta.exposure_time_s,
      meta.resin_volume_ml,
      meta.estimated_time_s,
      meta.filament_weight_g,
      meta.filament_length_mm,
      meta.filament_cost,
    ].some((value) => value !== null),
  );
  const geometryOnly = hasMeshGeometry && !hasEmbeddedSlicerMetadata;
  const resin = meta?.layer_count != null;
  const text = (value: string | null | undefined) =>
    value === null || value === undefined || value === "" ? null : value;
  const number = (value: number | null | undefined, format: (value: number) => string) =>
    value == null ? null : format(value);
  const left: SettingGroup[] = [
    {
      titleKey: "Printer & material",
      rows: [
        {
          labelKey: "Printer profile",
          value: text(meta?.printer_model),
          visible: preferences.printer_profile,
        },
        { labelKey: "Material", value: text(meta?.material_type), visible: preferences.material },
        {
          labelKey: "Filament profile",
          value: text(meta?.material_brand),
          visible: preferences.filament_profile,
        },
      ],
    },
    {
      titleKey: "Temperatures",
      rows: [
        {
          labelKey: "Nozzle temperature",
          value: number(meta?.nozzle_temperature_c, formatTemperature),
          visible: preferences.nozzle_temp && !resin,
        },
        {
          labelKey: "Bed temperature",
          value: number(meta?.bed_temperature_c, formatTemperature),
          visible: preferences.bed_temp && !resin,
        },
      ],
    },
    {
      titleKey: "Estimates",
      rows: [
        {
          labelKey: "Estimated time",
          value: number(meta?.estimated_time_s, formatDuration),
          visible: preferences.estimated_time,
        },
        {
          labelKey: "Filament weight",
          value: number(meta?.filament_weight_g, formatGrams),
          visible: preferences.filament_weight && !resin,
        },
        {
          labelKey: "Filament cost",
          value: number(meta?.filament_cost, formatCost),
          visible: preferences.filament_cost && !resin,
        },
        {
          labelKey: "RESIN VOLUME",
          value: number(
            meta?.resin_volume_ml,
            (value) => `${formatNumber(value, { maximumFractionDigits: 3 })} ml`,
          ),
          visible: meta?.resin_volume_ml != null,
        },
      ],
    },
  ];
  const structure: SettingGroup = {
    titleKey: "Layers & structure",
    rows: [
      {
        labelKey: "Layer height",
        value: number(meta?.layer_height_mm, formatMillimeters),
        visible: preferences.layer_height,
      },
      {
        labelKey: "First layer",
        value: number(meta?.first_layer_height_mm, formatMillimeters),
        visible: preferences.first_layer,
      },
      {
        labelKey: "Nozzle",
        value: number(meta?.nozzle_diameter_mm, formatMillimeters),
        visible: preferences.nozzle && !resin,
      },
      {
        labelKey: "Infill",
        value: number(meta?.infill_percent, formatPercent),
        visible: preferences.infill && !resin,
      },
      {
        labelKey: "Walls",
        value: number(meta?.wall_loops, String),
        visible: preferences.walls && !resin,
      },
      {
        labelKey: "Top / bottom",
        value:
          meta?.top_shell_layers == null && meta?.bottom_shell_layers == null
            ? null
            : `${meta?.top_shell_layers ?? uiText("Not recorded")} / ${meta?.bottom_shell_layers ?? uiText("Not recorded")}`,
        visible: preferences.top_bottom && !resin,
      },
      {
        labelKey: "Supports",
        value: meta?.support_material == null ? null : uiText(meta.support_material ? "Yes" : "No"),
        visible: preferences.supports && !resin,
      },
      {
        labelKey: "LAYERS",
        value: number(meta?.layer_count, formatNumber),
        visible: meta?.layer_count != null,
      },
      {
        labelKey: "EXPOSURE",
        value: number(
          meta?.exposure_time_s,
          (value) => `${formatNumber(value, { maximumFractionDigits: 3 })} s`,
        ),
        visible: meta?.exposure_time_s != null,
      },
    ],
  };
  const geometry: SettingGroup = {
    titleKey: "Mesh Geometry",
    rows: [
      {
        labelKey: "VOLUME",
        value: number(meta?.volume_mm3, (value) =>
          value < 1000
            ? `${formatNumber(value, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} mm³`
            : `${formatNumber(value / 1000, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} cm³`,
        ),
        visible: preferences.mesh_volume && meta?.volume_mm3 != null,
      },
      {
        labelKey: "TRIANGLES",
        value: number(meta?.triangle_count, formatNumber),
        visible: preferences.mesh_triangles && meta?.triangle_count != null,
      },
    ],
  };
  return (
    <Localized>
      <section className="@container/settings space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">{uiText("Print settings")}</h2>
          {headerActions}
        </div>
        {revisionPicker}
        {geometryOnly ? (
          <p className="text-sm leading-relaxed text-muted-foreground">
            {uiText(
              "This file contains mesh geometry only. Printer, material, and slicer settings are not embedded. Add a sliced G-code or 3MF revision to capture them.",
            )}
          </p>
        ) : (
          <>
            {!hasEmbeddedSlicerMetadata && (
              <p className="text-sm text-muted-foreground">
                {uiText(
                  "No print settings recorded yet. Add a sliced G-code revision to capture them.",
                )}
              </p>
            )}
            <div className="grid gap-6 @2xl/settings:grid-cols-2">
              <div className="space-y-5">
                {left.map((group) => (
                  <Group key={group.titleKey} group={group} />
                ))}
              </div>
              <Group group={structure} />
            </div>
          </>
        )}
        <Group group={geometry} />
        {preferences.slicer_info && (
          <p className="text-xs text-muted-foreground">
            {uiText("Sliced with ")}
            {meta?.slicer_name ? (
              <strong className="font-medium text-foreground">
                {meta.slicer_name}
                {meta.slicer_version ? ` ${meta.slicer_version}` : ""}
              </strong>
            ) : (
              uiText("Not recorded")
            )}
          </p>
        )}
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </section>
    </Localized>
  );
}

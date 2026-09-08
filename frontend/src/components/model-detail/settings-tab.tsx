"use client";

import { formatNumber } from "@/lib/format";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { MetadataPreferences } from "@/lib/metadata-preferences";
import { MetadataRead } from "@/types";

import { PrintSettingRow } from "./presentation";
import { SettingRow } from "./setting-row";
import { Localized } from "@/components/ui/localized";

export function SettingsTab({
  meta,
  printSettingRows,
  preferences,
}: {
  meta: MetadataRead | null | undefined;
  printSettingRows: PrintSettingRow[];
  preferences: MetadataPreferences;
}) {
  useUiLocale();
  const hasMeshGeometry = Boolean(meta?.volume_mm3 || meta?.triangle_count);
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
      meta.estimated_time_s,
      meta.filament_weight_g,
      meta.filament_length_mm,
      meta.filament_cost,
    ].some((value) => value !== null && value !== undefined),
  );
  const geometryOnly = hasMeshGeometry && !hasEmbeddedSlicerMetadata;
  return (
    <Localized>
      <>
        {printSettingRows.length === 0 && !geometryOnly && (
          <div className="rounded border border-outline-variant bg-surface px-3 py-3">
            <p className="font-mono text-xs leading-relaxed text-on-surface-variant">
              {uiText(
                "No print settings recorded yet. Add a sliced G-code revision to capture them.",
              )}
            </p>
          </div>
        )}
        {printSettingRows.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold text-on-surface mb-4 pb-1 border-b border-outline-variant">
              {uiText("Print Settings")}
            </h2>
            <div className="bg-surface border border-outline-variant rounded flex flex-col">
              {printSettingRows.map((row, index) => (
                <SettingRow
                  key={row.label}
                  label={row.label}
                  value={row.value}
                  chip={row.chip}
                  highlight={row.highlight}
                  last={index === printSettingRows.length - 1}
                />
              ))}
            </div>
          </section>
        )}

        {geometryOnly && (
          <div className="rounded border border-outline-variant bg-surface px-3 py-3">
            <p className="font-mono text-xs leading-relaxed text-on-surface-variant">
              {uiText(
                "This file contains mesh geometry only. Printer, material, and slicer settings are not embedded. Add a sliced G-code or 3MF revision to capture them.",
              )}
            </p>
          </div>
        )}

        {/* Mesh Geometry */}
        {((preferences.mesh_volume && meta?.volume_mm3) ||
          (preferences.mesh_triangles && meta?.triangle_count)) && (
          <section>
            <h2 className="text-lg font-semibold text-on-surface mb-4 pb-1 border-b border-outline-variant">
              {uiText("Mesh Geometry")}
            </h2>
            <div className="bg-surface border border-outline-variant rounded flex flex-col">
              {preferences.mesh_volume && meta?.volume_mm3 && (
                <SettingRow
                  label={uiText("VOLUME")}
                  value={
                    meta.volume_mm3 < 1000
                      ? `${formatNumber(meta.volume_mm3, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} mm³`
                      : `${formatNumber(meta.volume_mm3 / 1000, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} cm³`
                  }
                  last={!preferences.mesh_triangles || !meta?.triangle_count}
                />
              )}
              {preferences.mesh_triangles && meta?.triangle_count && (
                <SettingRow
                  label={uiText("TRIANGLES")}
                  value={meta.triangle_count.toLocaleString(currentLocale())}
                  last
                />
              )}
            </div>
          </section>
        )}

        {/* Slicer info */}
        {preferences.slicer_info && meta?.slicer_name && (
          <p className="font-mono text-xs text-on-surface-variant">
            {uiText("Sliced with ")}
            {meta.slicer_name}
            {meta.slicer_version
              ? uiText(" v{value1}", { value1: String(meta.slicer_version) })
              : ""}
          </p>
        )}
      </>
    </Localized>
  );
}

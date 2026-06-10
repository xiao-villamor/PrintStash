"use client";

import { MetadataPreferences } from "@/lib/metadata-preferences";
import { MetadataRead } from "@/types";

import { PrintSettingRow } from "./presentation";
import { SettingRow } from "./setting-row";

export function SettingsTab({
  meta,
  printSettingRows,
  preferences,
}: {
  meta: MetadataRead | null | undefined;
  printSettingRows: PrintSettingRow[];
  preferences: MetadataPreferences;
}) {
  return (
    <>
      {printSettingRows.length === 0 && (
        <p className="font-mono text-xs text-[var(--on-surface-variant)]">
          No print settings recorded yet. Add a sliced G-code revision to capture them.
        </p>
      )}
      {printSettingRows.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
            Print Settings
          </h2>
          <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded flex flex-col">
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

      {/* Mesh Geometry */}
      {((preferences.mesh_volume && meta?.volume_mm3)
        || (preferences.mesh_triangles && meta?.triangle_count)) && (
        <section>
          <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
            Mesh Geometry
          </h2>
          <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded flex flex-col">
            {preferences.mesh_volume && meta?.volume_mm3 && (
              <SettingRow
                label="VOLUME"
                value={meta.volume_mm3 < 1000 ? `${meta.volume_mm3.toFixed(1)} mm³` : `${(meta.volume_mm3 / 1000).toFixed(2)} cm³`}
                last={!preferences.mesh_triangles || !meta?.triangle_count}
              />
            )}
            {preferences.mesh_triangles && meta?.triangle_count && (
              <SettingRow label="TRIANGLES" value={meta.triangle_count.toLocaleString()} last />
            )}
          </div>
        </section>
      )}

      {/* Slicer info */}
      {preferences.slicer_info && meta?.slicer_name && (
        <p className="font-mono text-xs text-[var(--on-surface-variant)]">
          Sliced with {meta.slicer_name}
          {meta.slicer_version ? ` v${meta.slicer_version}` : ""}
        </p>
      )}
    </>
  );
}

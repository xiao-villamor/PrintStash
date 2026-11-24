import { useProjectProfile } from "@/lib/queries/project-profile";
import { formatNumber } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { formatPlateEstimate } from "./format-plate-estimate";
import { InspectorSection, PropertyList, PropertyRow, SoftCard } from "./inspector-section";

/** Saved project-wide settings; object overrides and actual print results remain separate. */
export function ProjectProfile({
  modelId,
  fileId,
  showPlateEstimates = true,
}: {
  modelId: number;
  fileId: number;
  showPlateEstimates?: boolean;
}) {
  useUiLocale();
  const query = useProjectProfile(modelId, fileId);
  const profile = query.data?.profile;
  const unknown = uiText("Not available in this file");
  const number = (value: number | null | undefined, unit = "") =>
    value == null ? unknown : `${formatNumber(value)}${unit}`;
  return (
    <InspectorSection title={uiText("3MF project settings")}>
      <SoftCard>
        {profile ? (
          <>
            {profile.name && <p className="wrap-anywhere px-4 py-2.5 text-sm">{profile.name}</p>}
            <PropertyList label={uiText("3MF project settings")}>
              <PropertyRow label={uiText("Printer")}>{profile.printer ?? unknown}</PropertyRow>
              <PropertyRow label={uiText("Material")}>
                {profile.materials.length ? profile.materials.join(", ") : unknown}
              </PropertyRow>
              <PropertyRow label={uiText("Nozzle diameter")}>
                {profile.nozzle_diameters_mm.length
                  ? profile.nozzle_diameters_mm.map((v) => number(v, " mm")).join(", ")
                  : unknown}
              </PropertyRow>
              <PropertyRow label={uiText("Layer height")}>
                {number(profile.layer_height_mm, " mm")}
              </PropertyRow>
              <PropertyRow label={uiText("Walls")}>{number(profile.wall_loops)}</PropertyRow>
              <PropertyRow label={uiText("Infill")}>
                {number(profile.infill_percent, "%")}
              </PropertyRow>
              {showPlateEstimates &&
                (profile.plates.length ? (
                  profile.plates.map((plate) => (
                    <PropertyRow
                      key={plate.number}
                      label={uiText("Plate {value1}", { value1: String(plate.number) })}
                    >
                      {plate.estimated_seconds == null
                        ? unknown
                        : formatPlateEstimate("estimated_time_s", plate.estimated_seconds)}{" "}
                      ·{" "}
                      {plate.filament_grams == null
                        ? unknown
                        : formatPlateEstimate("filament_weight_g", plate.filament_grams)}
                    </PropertyRow>
                  ))
                ) : (
                  <PropertyRow label={uiText("Estimate included in the file")}>
                    {unknown}
                  </PropertyRow>
                ))}
            </PropertyList>
            {showPlateEstimates && profile.plates.length > 0 && (
              <p className="px-4 py-2 text-xs text-muted-foreground">
                {uiText("Estimate included in the file")}
              </p>
            )}
            <p className="px-4 py-2.5 text-xs text-muted-foreground">
              {uiText(
                "Saved project settings; individual objects may override them. Estimates are not measured print results or a compatibility guarantee.",
              )}
            </p>
          </>
        ) : (
          <p className="px-4 py-3 text-sm text-muted-foreground">
            {query.isError
              ? uiText("Project settings could not be loaded.")
              : query.data?.state === "failed"
                ? uiText("Project settings could not be read. The original file is unchanged.")
                : query.data?.state === "skipped"
                  ? uiText("This file has no supported project settings.")
                  : query.data?.state === "disabled" || query.data?.state === "cancelled"
                    ? uiText("Project settings have not been checked.")
                    : uiText("Reading project settings…")}
          </p>
        )}
      </SoftCard>
    </InspectorSection>
  );
}

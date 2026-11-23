import { useProjectProfile } from "@/lib/queries/project-profile";
import { formatNumber } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";

/** Saved project-wide settings; object overrides and actual print results remain separate. */
export function ProjectProfile({ modelId, fileId }: { modelId: number; fileId: number }) {
  useUiLocale();
  const query = useProjectProfile(modelId, fileId);
  const profile = query.data?.profile;
  const unknown = uiText("Not available in this file");
  const number = (value: number | null | undefined, unit = "") =>
    value == null ? unknown : `${formatNumber(value)}${unit}`;
  return (
    <section
      className="mt-4 border-t border-border pt-3"
      aria-label={uiText("3MF project settings")}
    >
      <h3 className="text-xs font-medium">{uiText("3MF project settings")}</h3>
      {profile ? (
        <>
          {profile.name && <p className="mt-2 wrap-anywhere text-sm">{profile.name}</p>}
          <dl className="model-information-facts">
            <dt>{uiText("Printer")}</dt>
            <dd>{profile.printer ?? unknown}</dd>
            <dt>{uiText("Material")}</dt>
            <dd>{profile.materials.length ? profile.materials.join(", ") : unknown}</dd>
            <dt>{uiText("Nozzle diameter")}</dt>
            <dd>
              {profile.nozzle_diameters_mm.length
                ? profile.nozzle_diameters_mm.map((v) => number(v, " mm")).join(", ")
                : unknown}
            </dd>
            <dt>{uiText("Layer height")}</dt>
            <dd>{number(profile.layer_height_mm, " mm")}</dd>
            <dt>{uiText("Walls")}</dt>
            <dd>{number(profile.wall_loops)}</dd>
            <dt>{uiText("Infill")}</dt>
            <dd>{number(profile.infill_percent, "%")}</dd>
          </dl>
          <p className="mt-2 text-xs font-medium">{uiText("Estimate included in the file")}</p>
          {profile.plates.length ? (
            profile.plates.map((plate) => (
              <dl key={plate.number} className="model-information-facts">
                <dt>{uiText("Plate {value1}", { value1: String(plate.number) })}</dt>
                <dd>
                  {number(
                    plate.estimated_seconds == null ? null : plate.estimated_seconds / 60,
                    " min",
                  )}{" "}
                  · {number(plate.filament_grams, " g")}
                </dd>
              </dl>
            ))
          ) : (
            <p className="text-xs text-muted-foreground">{unknown}</p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            {uiText(
              "Saved project settings; individual objects may override them. Estimates are not measured print results or a compatibility guarantee.",
            )}
          </p>
        </>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
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
    </section>
  );
}

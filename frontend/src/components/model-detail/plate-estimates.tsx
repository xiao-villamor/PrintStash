import type { GcodePlates } from "@/lib/api/plates";
import { uiText } from "@/lib/locale";
import { formatPlateEstimate } from "./format-plate-estimate";

/** Estimates are slicer predictions for each plate, never the archive total. */
export function PlateEstimates({
  data,
  selected,
  showTime = true,
  showWeight = true,
  showCost = true,
}: {
  data: GcodePlates;
  selected: number | null;
  showTime?: boolean;
  showWeight?: boolean;
  showCost?: boolean;
}) {
  return (
    <section className="@container/estimates space-y-2" aria-label={uiText("Plate estimates")}>
      <h3 className="text-sm font-semibold">{uiText("Plate estimates")}</h3>
      {data.plates.map((number) => {
        const metadata = data.plate_metadata?.find((entry) => entry.number === number)?.metadata;
        const name = uiText("Plate {value1}", { value1: String(number).padStart(2, "0") });
        const plateName = data.identities?.find((plate) => plate.number === number)?.name;
        return (
          <section key={number} aria-label={name} className="detail-plate-estimate">
            <div className="min-w-0">
              <h4 className="text-sm font-medium">
                {name}
                {selected === number && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    {uiText("Selected plate")}
                  </span>
                )}
              </h4>
              {plateName && <p className="mt-0.5 wrap-anywhere text-sm">{plateName}</p>}
            </div>
            <dl>
              {showTime && (
                <div>
                  <dt>{uiText("Estimated time")}</dt>
                  <dd>{formatPlateEstimate("estimated_time_s", metadata?.estimated_time_s)}</dd>
                </div>
              )}
              {showWeight && (
                <div>
                  <dt>{uiText("Filament weight")}</dt>
                  <dd>{formatPlateEstimate("filament_weight_g", metadata?.filament_weight_g)}</dd>
                </div>
              )}
              {showCost && (
                <div>
                  <dt>{uiText("Filament cost")}</dt>
                  <dd>{formatPlateEstimate("filament_cost", metadata?.filament_cost)}</dd>
                </div>
              )}
            </dl>
          </section>
        );
      })}
    </section>
  );
}

import type { GcodePlates } from "@/lib/api/plates";
import { formatCost, formatNumber } from "@/lib/format";
import { currentLocale, uiText } from "@/lib/locale";

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
  const unit = (value: number, name: "hour" | "minute" | "second") =>
    new Intl.NumberFormat(currentLocale(), {
      style: "unit",
      unit: name,
      unitDisplay: "narrow",
    }).format(value);
  const duration = (seconds: number | null | undefined) => {
    if (seconds == null) return uiText("Not recorded");
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return [h ? unit(h, "hour") : null, h || m ? unit(m, "minute") : null, unit(s, "second")]
      .filter(Boolean)
      .join(" ");
  };
  return (
    <section className="space-y-2" aria-label={uiText("Plate estimates")}>
      <h3 className="text-sm font-semibold">{uiText("Plate estimates")}</h3>
      {data.plates.map((number) => {
        const metadata = data.plate_metadata?.find((entry) => entry.number === number)?.metadata;
        const name = uiText("Plate {value1}", { value1: String(number).padStart(2, "0") });
        return (
          <section key={number} aria-label={name} className="border-b border-border pb-2">
            <h4 className="text-sm font-medium">
              {name}
              {selected === number && (
                <span className="ml-2 text-xs text-muted-foreground">
                  {uiText("Selected plate")}
                </span>
              )}
            </h4>
            <dl className="detail-overview-facts">
              {showTime && (
                <div>
                  <dt>{uiText("Estimated time")}</dt>
                  <dd>{duration(metadata?.estimated_time_s)}</dd>
                </div>
              )}
              {showWeight && (
                <div>
                  <dt>{uiText("Filament weight")}</dt>
                  <dd>
                    {metadata?.filament_weight_g == null
                      ? uiText("Not recorded")
                      : `${formatNumber(metadata.filament_weight_g, { maximumFractionDigits: 2 })} g`}
                  </dd>
                </div>
              )}
              {showCost && (
                <div>
                  <dt>{uiText("Filament cost")}</dt>
                  <dd>
                    {metadata?.filament_cost == null
                      ? uiText("Not recorded")
                      : formatCost(metadata.filament_cost)}
                  </dd>
                </div>
              )}
            </dl>
          </section>
        );
      })}
    </section>
  );
}

import type { GcodePlates } from "@/lib/api/plates";
import { uiText } from "@/lib/locale";
import { formatPlateEstimate } from "./format-plate-estimate";

export function OverviewPlateEstimates({
  data,
  selected,
}: {
  data: GcodePlates;
  selected: number | null;
}) {
  const rows = data.plates.map((number) => ({
    number,
    name: data.identities?.find((plate) => plate.number === number)?.name,
    metadata: data.plate_metadata?.find((plate) => plate.number === number)?.metadata,
  }));
  const showCost = rows.some((row) => row.metadata?.filament_cost != null);
  return (
    <table className="detail-overview-plates">
      <caption>
        <h3>{uiText("Plate estimates")}</h3>
      </caption>
      <thead>
        <tr>
          <th scope="col">{uiText("Plate")}</th>
          <th scope="col">{uiText("Estimated time")}</th>
          <th scope="col">{uiText("Filament weight")}</th>
          {showCost && <th scope="col">{uiText("Filament cost")}</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map(({ number, name, metadata }) => (
          <tr
            key={number}
            className={number === selected ? "bg-accent text-accent-foreground" : undefined}
          >
            <th scope="row">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-xs text-muted-foreground">
                  {uiText("Plate {value1}", { value1: String(number).padStart(2, "0") })}
                </span>
                {selected === number && <span className="text-xs">{uiText("Selected plate")}</span>}
              </div>
              {name && <span className="block break-words font-medium">{name}</span>}
            </th>
            <td>{formatPlateEstimate("estimated_time_s", metadata?.estimated_time_s)}</td>
            <td>{formatPlateEstimate("filament_weight_g", metadata?.filament_weight_g)}</td>
            {showCost && <td>{formatPlateEstimate("filament_cost", metadata?.filament_cost)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

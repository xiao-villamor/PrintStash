import type { GcodePlateMetadata, GcodePlates } from "@/lib/api/plates";
import { uiText } from "@/lib/locale";
import { SoftCard } from "./inspector-section";
import { formatPlateEstimate } from "./format-plate-estimate";

/** The sum over every plate, or null when any plate lacks the time or weight. */
function plateTotal(data: GcodePlates): GcodePlateMetadata | null {
  if (data.plates.length < 2) return null;
  const rows = data.plates.map(
    (number) => data.plate_metadata?.find((entry) => entry.number === number)?.metadata,
  );
  if (!rows.every((row) => row?.estimated_time_s != null && row.filament_weight_g != null))
    return null;
  const sum = (key: keyof GcodePlateMetadata) =>
    rows.reduce((total, row) => total + (row?.[key] ?? 0), 0);
  return {
    estimated_time_s: sum("estimated_time_s"),
    filament_weight_g: sum("filament_weight_g"),
    filament_cost: rows.every((row) => row?.filament_cost != null) ? sum("filament_cost") : null,
  };
}

/**
 * Estimates are slicer predictions for each plate. The total row appears only
 * when every plate carries both estimates; a missing plate never becomes zero.
 */
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
  const columns: { key: keyof GcodePlateMetadata; label: string }[] = [
    ...(showTime ? [{ key: "estimated_time_s" as const, label: uiText("Time") }] : []),
    ...(showWeight ? [{ key: "filament_weight_g" as const, label: uiText("Filament") }] : []),
    ...(showCost ? [{ key: "filament_cost" as const, label: uiText("Cost") }] : []),
  ];
  const total = plateTotal(data);
  const cell = (metadata: GcodePlateMetadata | undefined, key: keyof GcodePlateMetadata) =>
    formatPlateEstimate(key, metadata?.[key]);
  return (
    <SoftCard className="divide-y-0">
      <table className="w-full text-sm" aria-label={uiText("Plate estimates")}>
        <thead>
          <tr className="text-start text-xs text-muted-foreground">
            <th scope="col" className="px-4 py-2 text-start font-normal">
              {uiText("Plate")}
            </th>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="whitespace-nowrap px-4 py-2 text-end font-normal"
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border border-t border-border">
          {data.plates.map((number) => {
            const metadata = data.plate_metadata?.find(
              (entry) => entry.number === number,
            )?.metadata;
            const name = uiText("Plate {value1}", { value1: String(number).padStart(2, "0") });
            const plateName = data.identities?.find((plate) => plate.number === number)?.name;
            return (
              <tr key={number} aria-label={name}>
                <th scope="row" className="min-w-0 px-4 py-2.5 text-start font-normal">
                  <span className="block whitespace-nowrap font-medium">
                    {name}
                    {selected === number && (
                      <span className="ms-2 text-xs font-normal text-muted-foreground">
                        {uiText("Selected plate")}
                      </span>
                    )}
                  </span>
                  {plateName && (
                    <span className="block max-w-[28ch] truncate text-xs text-muted-foreground">
                      {plateName}
                    </span>
                  )}
                </th>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className="whitespace-nowrap px-4 py-2.5 text-end tabular-nums"
                  >
                    {cell(metadata, column.key)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
        {total && (
          <tfoot className="border-t border-foreground/20 font-semibold">
            <tr>
              <th scope="row" className="px-4 py-2.5 text-start font-semibold">
                {uiText("All plates")}
              </th>
              {columns.map((column) => (
                <td
                  key={column.key}
                  className="whitespace-nowrap px-4 py-2.5 text-end tabular-nums"
                >
                  {cell(total, column.key)}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </SoftCard>
  );
}

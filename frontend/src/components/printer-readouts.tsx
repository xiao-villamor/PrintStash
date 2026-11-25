import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import type { PrinterSnapshot } from "@/types";

function finite(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The live readings a printer actually reports, in one glanceable strip. */
export function PrinterReadouts({ snapshot }: { snapshot: PrinterSnapshot }) {
  const hotend = finite(snapshot.extruder?.temperature);
  const bed = finite(snapshot.heater_bed?.temperature);
  const fan = finite(snapshot.fan?.speed);
  const readings = [
    hotend !== null && {
      label: uiText("Hotend"),
      value: uiText("{value1} °C", { value1: formatNumber(hotend, { maximumFractionDigits: 0 }) }),
    },
    bed !== null && {
      label: uiText("Bed"),
      value: uiText("{value1} °C", { value1: formatNumber(bed, { maximumFractionDigits: 0 }) }),
    },
    fan !== null &&
      fan >= 0 &&
      fan <= 1 && {
        label: uiText("Part fan"),
        value: `${formatNumber(fan * 100, { maximumFractionDigits: 0 })}%`,
      },
  ].filter((reading) => reading !== false);
  if (readings.length === 0) return null;
  return (
    <dl
      aria-label={uiText("Live readings")}
      className="flex w-fit max-w-full divide-x divide-border overflow-hidden rounded-lg border border-border bg-card"
    >
      {readings.map(({ label, value }) => (
        <div key={label} className="flex min-w-24 flex-col-reverse gap-0.5 px-4 py-2.5">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="font-mono text-xl font-semibold leading-none tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

import type { GcodePlateMetadata } from "@/lib/api/plates";
import { formatCost, formatNumber } from "@/lib/format";
import { currentLocale, uiText } from "@/lib/locale";

export function formatPlateEstimate(
  metric: keyof GcodePlateMetadata,
  value: number | null | undefined,
): string {
  if (value == null) return uiText("Not recorded");
  if (metric === "filament_cost") return formatCost(value);
  if (metric === "filament_weight_g")
    return uiText("{value1} g", { value1: formatNumber(value, { maximumFractionDigits: 2 }) });
  const unit = (value: number, name: "hour" | "minute" | "second") =>
    new Intl.NumberFormat(currentLocale(), {
      style: "unit",
      unit: name,
      unitDisplay: "narrow",
    }).format(value);
  const h = Math.floor(value / 3600);
  const m = Math.floor((value % 3600) / 60);
  const s = Math.floor(value % 60);
  return [h ? unit(h, "hour") : null, h || m ? unit(m, "minute") : null, unit(s, "second")]
    .filter(Boolean)
    .join(" ");
}

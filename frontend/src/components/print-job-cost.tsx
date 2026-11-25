import { formatNumber } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { JobProductionCost } from "@/types/production-costing";

export function PrintJobCost({ cost }: { cost: JobProductionCost }) {
  useUiLocale();
  const partial = cost.material === null || cost.machine === null;
  const label = partial
    ? cost.estimated
      ? uiText("Estimated partial cost")
      : uiText("Partial cost")
    : cost.estimated
      ? uiText("Estimated print cost")
      : uiText("Print cost");
  const amount = (value: number) =>
    formatNumber(value, { style: "currency", currency: cost.currency, maximumFractionDigits: 4 });

  return (
    <div className="text-xs text-muted-foreground">
      <span className="font-medium text-foreground">
        {label} · {amount(cost.total)}
      </span>
      <div className="flex flex-wrap gap-x-2.5">
        {cost.material !== null && (
          <span>
            {uiText("Material")} · {amount(cost.material)}
          </span>
        )}
        {cost.machine !== null && (
          <span>
            {uiText("Machine")} · {amount(cost.machine)}
          </span>
        )}
      </div>
    </div>
  );
}

export * from "@printstash/domain/card-metrics";
import { CARD_METRIC_OPTIONS as options, type CardMetrics } from "@printstash/domain/card-metrics";
import { knownUiText } from "./locale";

export const CARD_METRIC_OPTIONS = options.map((option) => ({
  ...option,
  get label() {
    return knownUiText(option.label);
  },
  get abbr() {
    return knownUiText(option.abbr);
  },
}));

// Keep the default Library card order for preferences saved before direct editing.
export function cardMetricsInDisplayOrder(metrics: CardMetrics): CardMetrics {
  const [first, second, third] = metrics;
  return first === "layer_height" && second === "print_time" && third === "filament_weight"
    ? [second, third, first]
    : [first, second, third];
}

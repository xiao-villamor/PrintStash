export * from "@printstash/domain/card-metrics";
import { CARD_METRIC_OPTIONS as options } from "@printstash/domain/card-metrics";
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

import { formatCurrency as format, CURRENCY_OPTIONS as options } from "@printstash/domain/currency";
import { currentLocale } from "./locale";

export const CURRENCY_OPTIONS = options.map((option) => ({
  code: option.code,
  get label() {
    const name =
      new Intl.DisplayNames(currentLocale(), { type: "currency" }).of(option.code) ?? option.code;
    const suffix = option.label.match(/ (\([^)]*\))$/)?.[0] ?? "";
    return `${option.code} — ${name}${suffix}`;
  },
}));
export type { CurrencyOption } from "@printstash/domain/currency";
export const formatCurrency = (value: number | null | undefined, code: string) =>
  format(value, code, currentLocale());

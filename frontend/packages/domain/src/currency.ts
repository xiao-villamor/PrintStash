/** Currency helpers shared by settings and every cost display. */

export interface CurrencyOption {
  code: string;
  label: string;
}

export const CURRENCY_OPTIONS: CurrencyOption[] = [
  { code: "USD", label: "USD — US Dollar ($)" },
  { code: "EUR", label: "EUR — Euro (€)" },
  { code: "GBP", label: "GBP — British Pound (£)" },
  { code: "CAD", label: "CAD — Canadian Dollar ($)" },
  { code: "AUD", label: "AUD — Australian Dollar ($)" },
  { code: "JPY", label: "JPY — Japanese Yen (¥)" },
  { code: "CNY", label: "CNY — Chinese Yuan (¥)" },
  { code: "INR", label: "INR — Indian Rupee (₹)" },
  { code: "CHF", label: "CHF — Swiss Franc" },
  { code: "SEK", label: "SEK — Swedish Krona (kr)" },
  { code: "NOK", label: "NOK — Norwegian Krone (kr)" },
  { code: "DKK", label: "DKK — Danish Krone (kr)" },
  { code: "PLN", label: "PLN — Polish Złoty (zł)" },
  { code: "BRL", label: "BRL — Brazilian Real (R$)" },
  { code: "MXN", label: "MXN — Mexican Peso ($)" },
];

export function formatCurrency(value: number | null | undefined, code: string): string {
  if (value == null) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code || "USD",
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${code}`;
  }
}

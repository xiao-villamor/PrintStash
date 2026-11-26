import { uiText } from "@/lib/locale";
import type { PrinterExpenses } from "@/types/production-costing";

export const expenseFields = [
  {
    key: "acquisition_cost",
    get label() {
      return uiText("Purchase cost");
    },
    money: true,
  },
  {
    key: "lifetime_hours",
    get label() {
      return uiText("Expected lifetime (print hours)");
    },
    money: false,
  },
  {
    key: "lifetime_maintenance",
    get label() {
      return uiText("Maintenance over lifetime");
    },
    money: true,
  },
  {
    key: "power_watts",
    get label() {
      return uiText("Average power (W)");
    },
    money: false,
  },
  {
    key: "electricity_per_kwh",
    get label() {
      return uiText("Electricity per kWh");
    },
    money: true,
  },
  {
    key: "extra_per_hour",
    get label() {
      return uiText("Additional cost per print hour");
    },
    money: true,
  },
  {
    key: "failure_rate_percent",
    get label() {
      return uiText("Print failure rate (%)");
    },
    money: false,
  },
] as const;

export type ExpenseDraft = { [Key in keyof PrinterExpenses]: string };

// A budgeting assumption used only when a profile's operating costs are set.
export const DEFAULT_LIFETIME_HOURS = 5000;

type PublishedPrintPower = { watts: number; source: string; pla: boolean };

const prusaMkSeriesPower = {
  watts: 80,
  source: "https://help.prusa3d.com/article/faq-frequently-asked-questions_1932",
  pla: true,
};

const publishedPrintPower = new Map<string, PublishedPrintPower>([
  ["bambu lab a1", { watts: 95, source: "https://wiki.bambulab.com/en/a1/manual/faq", pla: true }],
  [
    "bambu lab a1 mini",
    { watts: 57, source: "https://wiki.bambulab.com/en/a1-mini/manual/faq", pla: false },
  ],
  ["prusa mk3s+", prusaMkSeriesPower],
  ["prusa mk4", prusaMkSeriesPower],
  [
    "prusa mk4s",
    {
      watts: 80,
      source: "https://www.prusa3d.com/product/original-prusa-mk4s-3d-printer/",
      pla: true,
    },
  ],
]);

export function averagePrintPower(model: string) {
  return publishedPrintPower.get(model.trim().toLowerCase());
}

export function machineCostBreakdown(expenses: PrinterExpenses) {
  return {
    depreciation: expenses.acquisition_cost / expenses.lifetime_hours,
    maintenance: expenses.lifetime_maintenance / expenses.lifetime_hours,
    electricity: (expenses.power_watts / 1000) * expenses.electricity_per_kwh,
    additional: expenses.extra_per_hour,
  };
}

export function expenseDraft(expenses: PrinterExpenses | null) {
  return {
    acquisition_cost: expenses ? String(expenses.acquisition_cost) : "",
    lifetime_hours: expenses ? String(expenses.lifetime_hours) : "",
    lifetime_maintenance: expenses ? String(expenses.lifetime_maintenance) : "",
    power_watts: expenses ? String(expenses.power_watts) : "",
    electricity_per_kwh: expenses ? String(expenses.electricity_per_kwh) : "",
    extra_per_hour: expenses ? String(expenses.extra_per_hour) : "",
    failure_rate_percent: expenses ? String(expenses.failure_rate_percent) : "",
  };
}

export function expensesChanged(previous: ExpenseDraft, current: ExpenseDraft) {
  return expenseFields.some(({ key }) => previous[key] !== current[key]);
}

export function parseExpenses(draft: ExpenseDraft, model: string): PrinterExpenses | null {
  if (!expenseFields.some(({ key }) => draft[key].trim())) return null;
  return {
    acquisition_cost: Number(draft.acquisition_cost),
    lifetime_hours: draft.lifetime_hours.trim()
      ? Number(draft.lifetime_hours)
      : DEFAULT_LIFETIME_HOURS,
    lifetime_maintenance: Number(draft.lifetime_maintenance),
    power_watts: draft.power_watts.trim()
      ? Number(draft.power_watts)
      : (averagePrintPower(model)?.watts ?? 0),
    electricity_per_kwh: Number(draft.electricity_per_kwh),
    extra_per_hour: Number(draft.extra_per_hour),
    failure_rate_percent: Number(draft.failure_rate_percent),
  };
}

export function expenseProblem(expenses: PrinterExpenses | null) {
  if (expenses === null) return null;
  if (expenses.lifetime_hours < 1)
    return uiText("Enter an expected lifetime of at least one print hour.");
  if (
    !Number.isFinite(expenses.failure_rate_percent) ||
    expenses.failure_rate_percent < 0 ||
    expenses.failure_rate_percent > 99.99
  )
    return uiText("Enter a failure rate from 0 to 99.99%.");
  if (
    expenseFields.some(
      ({ key }) => !Number.isFinite(expenses[key]) || expenses[key] < 0 || expenses[key] > 1000000,
    )
  )
    return uiText("Enter expenses between 0 and 1,000,000.");
  return null;
}

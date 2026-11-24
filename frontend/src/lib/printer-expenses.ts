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
] as const;

export type ExpenseDraft = { [Key in keyof PrinterExpenses]: string };

export function expenseDraft(expenses: PrinterExpenses | null) {
  return {
    acquisition_cost: expenses ? String(expenses.acquisition_cost) : "",
    lifetime_hours: expenses ? String(expenses.lifetime_hours) : "",
    lifetime_maintenance: expenses ? String(expenses.lifetime_maintenance) : "",
    power_watts: expenses ? String(expenses.power_watts) : "",
    electricity_per_kwh: expenses ? String(expenses.electricity_per_kwh) : "",
    extra_per_hour: expenses ? String(expenses.extra_per_hour) : "",
  };
}

export function expensesChanged(previous: ExpenseDraft, current: ExpenseDraft) {
  return expenseFields.some(({ key }) => previous[key] !== current[key]);
}

export function parseExpenses(draft: ExpenseDraft): PrinterExpenses | null {
  if (!expenseFields.some(({ key }) => draft[key].trim())) return null;
  return {
    acquisition_cost: Number(draft.acquisition_cost),
    lifetime_hours: Number(draft.lifetime_hours),
    lifetime_maintenance: Number(draft.lifetime_maintenance),
    power_watts: Number(draft.power_watts),
    electricity_per_kwh: Number(draft.electricity_per_kwh),
    extra_per_hour: Number(draft.extra_per_hour),
  };
}

export function expenseProblem(expenses: PrinterExpenses | null) {
  if (expenses === null) return null;
  if (expenses.lifetime_hours < 1)
    return uiText("Enter an expected lifetime of at least one print hour.");
  if (
    expenseFields.some(
      ({ key }) => !Number.isFinite(expenses[key]) || expenses[key] < 0 || expenses[key] > 1000000,
    )
  )
    return uiText("Enter expenses between 0 and 1,000,000.");
  return null;
}

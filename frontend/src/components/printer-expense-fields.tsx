import { useId } from "react";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import {
  averagePrintPower,
  DEFAULT_LIFETIME_HOURS,
  expenseFields,
  expenseProblem,
  parseExpenses,
  type ExpenseDraft,
} from "@/lib/printer-expenses";

export function PrinterExpenseFields({
  draft,
  model,
  currency,
  disabled,
  focus = false,
  onChange,
}: {
  draft: ExpenseDraft;
  model: string;
  currency: string | null;
  disabled: boolean;
  focus?: boolean;
  onChange: (draft: ExpenseDraft) => void;
}) {
  const hintId = useId();
  const problem = expenseProblem(parseExpenses(draft, model));
  const power = averagePrintPower(model);
  const hints = {
    lifetime_hours: uiText(
      "Defaults to {value1} print hours. Use your planned replacement interval.",
      {
        value1: formatNumber(DEFAULT_LIFETIME_HOURS),
      },
    ),
    power_watts: power ? (
      <a
        href={power.source}
        target="_blank"
        rel="noreferrer"
        className="underline underline-offset-2 hover:text-foreground"
      >
        {uiText(
          power.pla
            ? "Published average of {value1} W for PLA"
            : "Published average of {value1} W while printing",
          { value1: formatNumber(power.watts) },
        )}
      </a>
    ) : (
      uiText(
        "Enter average watts measured during a typical print. Leave blank to omit electricity.",
      )
    ),
  };
  const placeholders = {
    lifetime_hours: String(DEFAULT_LIFETIME_HOURS),
    power_watts: power ? String(power.watts) : undefined,
  };
  return (
    <fieldset disabled={disabled} className="col-span-full space-y-3 border-t pt-3">
      <legend className="pr-2 text-sm font-semibold">{uiText("Operating costs")}</legend>
      <p className="text-xs text-muted-foreground">
        {uiText(
          "These rates are used in production estimates. Enter 0 for expenses you do not incur.",
        )}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {expenseFields.map(({ key, label, money }) => {
          const hasHint = key === "lifetime_hours" || key === "power_watts";
          const fieldHintId = hasHint ? `${hintId}-${key}` : undefined;
          return (
            <FormField
              key={key}
              label={money && currency ? `${label} (${currency})` : label}
              hint={hasHint ? hints[key] : undefined}
              hintId={fieldHintId}
            >
              <Input
                type="number"
                aria-describedby={fieldHintId}
                placeholder={hasHint ? placeholders[key] : undefined}
                data-autofocus={focus && key === "acquisition_cost" ? true : undefined}
                min={key === "lifetime_hours" ? 1 : 0}
                max={1000000}
                step="any"
                value={draft[key]}
                onChange={(event) => onChange({ ...draft, [key]: event.target.value })}
              />
            </FormField>
          );
        })}
      </div>
      {problem && <p className="text-xs text-destructive">{problem}</p>}
    </fieldset>
  );
}

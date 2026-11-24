import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { uiText } from "@/lib/locale";
import {
  expenseFields,
  expenseProblem,
  parseExpenses,
  type ExpenseDraft,
} from "@/lib/printer-expenses";

export function PrinterExpenseFields({
  draft,
  currency,
  disabled,
  onChange,
}: {
  draft: ExpenseDraft;
  currency: string | null;
  disabled: boolean;
  onChange: (draft: ExpenseDraft) => void;
}) {
  const problem = expenseProblem(parseExpenses(draft));
  return (
    <fieldset disabled={disabled} className="col-span-full space-y-3 border-t pt-3">
      <legend className="pr-2 text-sm font-semibold">{uiText("Operating costs")}</legend>
      <p className="text-xs text-muted-foreground">
        {uiText(
          "These rates are used in production estimates. Enter 0 for expenses you do not incur.",
        )}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {expenseFields.map(({ key, label, money }) => (
          <FormField key={key} label={money && currency ? `${label} (${currency})` : label}>
            <Input
              type="number"
              min={key === "lifetime_hours" ? 1 : 0}
              max={1000000}
              step="any"
              value={draft[key]}
              onChange={(event) => onChange({ ...draft, [key]: event.target.value })}
            />
          </FormField>
        ))}
      </div>
      {problem && <p className="text-xs text-destructive">{problem}</p>}
    </fieldset>
  );
}

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { uiText } from "@/lib/locale";

export function FilamentSpoolPrice({
  currency,
  disabled,
  onApply,
}: {
  currency: string | null;
  disabled: boolean;
  onApply: (cost: string) => void;
}) {
  const priceId = useId();
  const weightId = useId();
  const [price, setPrice] = useState("");
  const [weight, setWeight] = useState("");
  const cost = (Number(price) / Number(weight)) * 1000;
  const valid =
    price.trim() && weight.trim() && Number(weight) > 0 && Number.isFinite(cost) && cost >= 0;

  return (
    <fieldset className="col-span-full space-y-3 border-y py-3">
      <legend className="pr-2 text-sm font-medium">{uiText("Calculate from spool price")}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField
          htmlFor={priceId}
          label={currency ? `${uiText("Spool price")} (${currency})` : uiText("Spool price")}
        >
          <Input
            id={priceId}
            type="number"
            min="0"
            step="any"
            value={price}
            disabled={disabled}
            onChange={(event) => setPrice(event.target.value)}
          />
        </FormField>
        <FormField
          htmlFor={weightId}
          label={uiText("Net filament weight (g)")}
          hint={uiText("Exclude the empty spool.")}
        >
          <Input
            id={weightId}
            type="number"
            min="0"
            step="any"
            value={weight}
            disabled={disabled}
            onChange={(event) => setWeight(event.target.value)}
          />
        </FormField>
        <Button
          className="justify-self-start"
          size="sm"
          type="button"
          variant="outline"
          disabled={!valid || disabled}
          onClick={() => onApply(String(Number(cost.toFixed(4))))}
        >
          {uiText("Use calculated cost per kg")}
        </Button>
      </div>
    </fieldset>
  );
}

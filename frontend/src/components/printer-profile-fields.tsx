import { useId, useState } from "react";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
import { formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import { PRINTER_MODEL_OPTIONS } from "@/lib/printer-providers";

const NOZZLE_SIZES = ["0.2", "0.25", "0.3", "0.4", "0.5", "0.6", "0.8", "1"];
const CUSTOM = "__custom__";

function ProfileChoice({
  label,
  accessibleLabel,
  customLabel,
  value,
  choices,
  formatLabel = (choice) => choice,
  disabled,
  decimal,
  onChange,
}: {
  label: string;
  accessibleLabel?: string;
  customLabel: string;
  value: string;
  choices: readonly string[];
  formatLabel?: (choice: string) => string;
  disabled?: boolean;
  decimal?: boolean;
  onChange: (value: string) => void;
}) {
  const selectId = useId();
  const [enteringCustom, setEnteringCustom] = useState(false);
  const custom = enteringCustom || (value !== "" && !choices.includes(value));
  return (
    <div className="min-w-0 space-y-2">
      <FormField label={label} htmlFor={selectId}>
        <select
          id={selectId}
          className={`${inputClasses} whitespace-nowrap`}
          value={custom ? CUSTOM : value}
          aria-label={accessibleLabel}
          disabled={disabled}
          onChange={(event) => {
            const choice = event.target.value;
            setEnteringCustom(choice === CUSTOM);
            if (choice !== CUSTOM) onChange(choice);
          }}
        >
          <option value="">{uiText("Not specified")}</option>
          {choices.map((choice) => (
            <option key={choice} value={choice}>
              {formatLabel(choice)}
            </option>
          ))}
          <option value={CUSTOM}>{customLabel}</option>
        </select>
      </FormField>
      {custom && (
        <FormField label={customLabel}>
          <Input
            value={value}
            disabled={disabled}
            inputMode={decimal ? "decimal" : undefined}
            maxLength={decimal ? undefined : 128}
            onChange={(event) => onChange(event.target.value)}
          />
        </FormField>
      )}
    </div>
  );
}

export function PrinterProfileFields({
  id,
  model,
  nozzle,
  disabled,
  onModelChange,
  onNozzleChange,
}: {
  id?: number;
  model: string;
  nozzle: string;
  disabled?: boolean;
  onModelChange: (model: string) => void;
  onNozzleChange: (nozzle: string) => void;
}) {
  return (
    <div className="col-span-full grid items-start gap-4 sm:grid-cols-2">
      <ProfileChoice
        label={uiText("Printer model")}
        accessibleLabel={
          id === undefined ? undefined : uiText("Printer model {value1}", { value1: String(id) })
        }
        customLabel={uiText("Custom printer model")}
        choices={PRINTER_MODEL_OPTIONS}
        value={model}
        disabled={disabled}
        onChange={onModelChange}
      />
      <ProfileChoice
        label={uiText("Nozzle diameter")}
        accessibleLabel={
          id === undefined
            ? undefined
            : uiText("Printer nozzle diameter {value1}", { value1: String(id) })
        }
        customLabel={uiText("Custom nozzle diameter (mm)")}
        choices={NOZZLE_SIZES}
        formatLabel={(size) => `${formatNumber(Number(size))} mm`}
        value={nozzle}
        disabled={disabled}
        decimal
        onChange={onNozzleChange}
      />
    </div>
  );
}

import { useId, type ReactNode } from "react";

/** Visible choices keep the available printer and revision decisions together. */
export function ChoiceField<T extends string | number>({
  label,
  value,
  options,
  onChange,
  disabled = false,
  inline = false,
}: {
  label: ReactNode;
  value: T | null;
  options: readonly { value: T; label: ReactNode; description?: ReactNode; disabled?: boolean }[];
  onChange: (value: T) => void;
  disabled?: boolean;
  inline?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="detail-choice-field" disabled={disabled}>
      <legend>{label}</legend>
      <div className={inline ? "detail-visible-choices is-inline" : "detail-visible-choices"}>
        {options.map((option) => (
          <label key={option.value} data-selected={value === option.value}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
            />
            <span>
              <span>{option.label}</span>
              {option.description && <small>{option.description}</small>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

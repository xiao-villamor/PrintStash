import { useId } from "react";
import { Input, inputClasses } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { providerFields } from "@/lib/storage-provider-form";
import type { StorageProvider, StorageProviderConfigValues } from "@/types";

export function StorageProviderFields({
  provider,
  values,
  onChange,
  use = "vault",
  disabled,
  storedSecrets = [],
  editing = false,
  onClear,
}: {
  provider: StorageProvider;
  values: StorageProviderConfigValues;
  onChange: (name: string, value: string | number) => void;
  use?: string;
  disabled?: boolean;
  storedSecrets?: string[];
  editing?: boolean;
  onClear?: (name: string) => void;
}) {
  const prefix = useId();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {providerFields(provider, use).map((field) => {
        const stored = field.secret && storedSecrets.includes(field.name);
        const id = `${prefix}-${field.name}`;
        return (
          <div key={field.name} className="space-y-1.5">
            <label
              htmlFor={id}
              className="flex items-center justify-between gap-2 text-xs font-medium text-on-surface-variant"
            >
              <span>{field.label}</span>
              {!field.required && <span>Optional</span>}
            </label>
            {field.options?.length ? (
              <select
                id={id}
                className={inputClasses}
                disabled={disabled}
                value={String(values[field.name] ?? field.default ?? "")}
                onChange={(event) => onChange(field.name, event.target.value)}
              >
                {field.options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                id={id}
                type={field.secret ? "password" : field.input_type === "number" ? "number" : "text"}
                value={String(values[field.name] ?? "")}
                disabled={disabled}
                required={field.required && !stored}
                placeholder={stored ? "Stored — leave blank to keep" : undefined}
                autoComplete={field.secret ? "new-password" : "off"}
                aria-describedby={`${id}-help`}
                onChange={(event) =>
                  onChange(
                    field.name,
                    field.input_type === "number" ? Number(event.target.value) : event.target.value,
                  )
                }
              />
            )}
            <p id={`${id}-help`} className="text-xs text-on-surface-variant">
              {field.help}
              {stored ? " A value is currently stored." : ""}
            </p>
            {editing && stored && !field.required && onClear && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={() => onClear(field.name)}
              >
                {values[field.name] === ""
                  ? "Credential will be cleared"
                  : `Clear stored ${field.label.toLowerCase()}`}
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}

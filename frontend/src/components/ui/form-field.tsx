import type { ReactNode } from "react";

import { useFieldError } from "@/lib/form-validation";
import { uiText } from "@/lib/locale";
import { cn } from "@/lib/utils";

/**
 * One field: label, control, optional hint and an error under the control.
 * Required fields carry no mark; a field that may stay empty says "(optional)"
 * in the muted token. Wire the control with `aria-invalid` and
 * `aria-describedby` set to `errorId` when `error` is present.
 */
export function FormField({
  label,
  htmlFor,
  children,
  hint,
  hintId,
  error,
  errorId,
  optional,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  hint?: ReactNode;
  hintId?: string;
  error?: ReactNode;
  errorId?: string;
  optional?: boolean;
  className?: string;
}) {
  const contextError = useFieldError(htmlFor);
  const shownError = error ?? contextError;
  const title = (
    <span className="mb-1.5 block text-field font-medium text-foreground">
      {label}
      {optional && (
        <span className="ml-1 font-normal text-muted-foreground">{uiText("(optional)")}</span>
      )}
    </span>
  );

  return (
    <div className={cn("min-w-0", className)}>
      {htmlFor ? (
        <>
          <label className="block" htmlFor={htmlFor}>
            {title}
          </label>
          {children}
        </>
      ) : (
        <label className="block">
          {title}
          {children}
        </label>
      )}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {shownError && (
        <p id={errorId} role="alert" className="mt-1 text-xs text-destructive">
          {shownError}
        </p>
      )}
    </div>
  );
}

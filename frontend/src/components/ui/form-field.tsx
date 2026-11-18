import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function FormField({
  label,
  htmlFor,
  children,
  hint,
  hintId,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  hint?: ReactNode;
  hintId?: string;
  className?: string;
}) {
  const title = (
    <span className="mb-1.5 block text-field font-medium text-foreground">{label}</span>
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
    </div>
  );
}

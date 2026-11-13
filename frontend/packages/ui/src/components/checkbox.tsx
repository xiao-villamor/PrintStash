"use client";

import { forwardRef } from "react";

import { cn } from "../lib/utils";

export const Checkbox = forwardRef<
  HTMLButtonElement,
  {
    checked: boolean;
    onChange: (checked: boolean) => void;
    className?: string;
    ariaLabel?: string;
    ariaDescribedBy?: string;
    disabled?: boolean;
  }
>(({ checked, onChange, className, ariaLabel, ariaDescribedBy, disabled }, ref) => {
  return (
    <button
      ref={ref}
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={ariaLabel}
      aria-describedby={ariaDescribedBy}
      disabled={disabled}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onChange(!checked);
      }}
      className={cn(
        "flex h-5 w-5 items-center justify-center rounded border transition-[color,background-color,border-color,transform] duration-press active:scale-[0.95] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-50",
        checked
          ? "border-primary bg-card text-primary"
          : "border-input bg-card text-transparent hover:border-primary",
        className,
      )}
    >
      <span aria-hidden className="h-2.5 w-2.5 bg-current" />
    </button>
  );
});
Checkbox.displayName = "Checkbox";

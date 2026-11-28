"use client";

import { Check } from "lucide-react";
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
        "relative flex h-6 w-6 items-center justify-center rounded border after:absolute after:-inset-[3px] after:content-[''] pointer-coarse:after:-inset-[10px] border-input transition-[background-color,border-color,transform] duration-press hover:border-ring active:scale-[0.95] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-card disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none motion-reduce:active:scale-100",
        checked ? "bg-accent text-accent-foreground" : "bg-card",
        className,
      )}
    >
      {checked && <Check size={14} strokeWidth={2.5} aria-hidden />}
    </button>
  );
});
Checkbox.displayName = "Checkbox";

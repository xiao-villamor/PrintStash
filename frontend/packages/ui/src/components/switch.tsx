"use client";

import { forwardRef } from "react";

import { cn } from "../lib/utils";

/**
 * The one binary toggle: a square 36×20 track with its state also written as
 * text, so On/Off never depends on colour alone.
 */
export const Switch = forwardRef<
  HTMLButtonElement,
  {
    checked: boolean;
    onChange: (checked: boolean) => void;
    /** Accessible name of the switch itself. */
    label: string;
    /** Visible state words; pass `null` to hide them where a row already says it. */
    stateLabels?: { on: string; off: string } | null;
    disabled?: boolean;
    className?: string;
  }
>(({ checked, onChange, label, stateLabels = { on: "On", off: "Off" }, disabled, className }, ref) => (
  <span className={cn("inline-flex items-center gap-2", className)}>
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:cursor-disabled disabled:opacity-50",
        checked ? "bg-primary" : "bg-input",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "ml-0.5 inline-block h-4 w-4 bg-card transition-transform duration-press",
          checked ? "translate-x-4" : "translate-x-0",
        )}
      />
    </button>
    {stateLabels && (
      <span aria-hidden className="text-xs text-foreground">
        {checked ? stateLabels.on : stateLabels.off}
      </span>
    )}
  </span>
));
Switch.displayName = "Switch";

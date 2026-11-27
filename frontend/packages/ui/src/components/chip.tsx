/* eslint-disable react-refresh/only-export-components */
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "../lib/utils";

/**
 * A quiet status pill: a faint tinted fill, a small outline icon and a coloured
 * word. `neutral` carries no meaning; `success`, `warning` and `destructive` tint
 * lightly (10%) so a state reads at a glance without turning the list into a
 * rainbow.
 */
const chipVariants = cva(
  "inline-flex h-[22px] max-w-full items-center gap-1.5 whitespace-nowrap rounded-full pr-2 text-xs font-medium leading-none",
  {
    variants: {
      tone: {
        neutral: "bg-foreground/10 text-foreground/80",
        success: "bg-success/10 text-success",
        warning: "bg-warning/10 text-warning",
        destructive: "bg-destructive/10 text-destructive",
      },
      hasIcon: {
        true: "pl-1.5",
        false: "pl-2",
      },
    },
    defaultVariants: {
      tone: "neutral",
      hasIcon: false,
    },
  },
);

export interface ChipProps
  extends React.HTMLAttributes<HTMLSpanElement>, Pick<VariantProps<typeof chipVariants>, "tone"> {
  /** A 12px outline icon shown before the label; the chip hides it from assistive tech. */
  icon?: React.ReactNode;
}

function Chip({ className, tone, icon, children, ...props }: ChipProps) {
  return (
    <span
      data-slot="chip"
      data-tone={tone ?? "neutral"}
      className={cn(chipVariants({ tone, hasIcon: icon != null }), className)}
      {...props}
    >
      {icon != null && (
        <span aria-hidden className="flex shrink-0 [&>svg]:size-3 [&>svg]:shrink-0">
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}

export { Chip, chipVariants };

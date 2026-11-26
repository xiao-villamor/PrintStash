/* eslint-disable react-refresh/only-export-components */
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "../lib/utils";

/**
 * A quiet status pill: neutral fill, small outline icon, no colour. Only the
 * `destructive` tone is loud, so an exception stands out among ordinary states.
 */
const chipVariants = cva(
  "inline-flex h-[22px] max-w-full items-center gap-1.5 whitespace-nowrap rounded-full bg-foreground/10 pr-2 text-xs font-medium leading-none",
  {
    variants: {
      tone: {
        neutral: "text-foreground/80",
        destructive: "text-destructive",
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

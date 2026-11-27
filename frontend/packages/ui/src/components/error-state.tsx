import { AlertCircle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../lib/utils";
import { Button } from "./button";

export interface ErrorStateProps {
  /** What failed, in a sentence a person can act on. */
  message: ReactNode;
  /** Label of the retry button; the button shows only when `onRetry` is set. */
  retryLabel?: string;
  onRetry?: () => void;
  retrying?: boolean;
  /** Extra actions shown before Retry, e.g. "Back to Library". */
  actions?: ReactNode;
  /** `inline` sits inside a form or panel; `block` fills an empty surface. */
  layout?: "inline" | "block";
  className?: string;
}

/**
 * The one way a failed read is shown: alert icon, message and a Retry action in
 * a fixed position. Never pair it with a toast for the same failure.
 */
export function ErrorState({
  message,
  retryLabel = "Retry",
  onRetry,
  retrying,
  actions,
  layout = "inline",
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-center gap-3 text-sm",
        layout === "block"
          ? "flex-col justify-center px-6 py-12 text-center"
          : "flex-wrap rounded-md border border-border bg-muted/30 px-3 py-2",
        className,
      )}
    >
      <AlertCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden />
      <span className="min-w-0 flex-1 text-foreground">{message}</span>
      {actions}
      {onRetry && (
        <Button type="button" variant="outline" size="sm" loading={retrying} onClick={onRetry}>
          {retryLabel}
        </Button>
      )}
    </div>
  );
}

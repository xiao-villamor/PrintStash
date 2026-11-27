"use client";

import { type ReactNode, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { DURATION, useMountTransition, useOverlayBehavior } from "../lib/overlay";
import { cn } from "../lib/utils";

export interface ModalShellProps {
  open?: boolean;
  onClose: () => void;
  labelledBy?: string;
  describedBy?: string;
  className?: string;
  children: ReactNode;
}

/**
 * Low-level dialog chrome: portal, animated backdrop + panel, focus trap,
 * Escape, scroll lock. Use `Modal` for standard titled dialog chrome.
 */
export function ModalShell({
  open = true,
  onClose,
  labelledBy,
  describedBy,
  className,
  children,
}: ModalShellProps) {
  const { mounted, state } = useMountTransition(open, DURATION.fast);
  const panelRef = useRef<HTMLDivElement>(null);
  useOverlayBehavior(open, onClose, panelRef);

  if (!mounted) return null;
  return createPortal(
    <div className="fixed inset-0 z-overlay flex items-center justify-center p-4">
      <div
        data-state={state}
        onClick={onClose}
        aria-hidden
        className="absolute inset-0 bg-overlay-scrim transition-opacity duration-fast ease-out data-[state=closed]:opacity-0"
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        data-overlay-open={open}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        data-state={state}
        className={cn(
          "relative outline-none transition-[opacity,transform] duration-fast ease-out",
          "data-[state=closed]:scale-content-enter data-[state=closed]:opacity-0 motion-reduce:data-[state=closed]:scale-100",
          className,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** Accessible label injected by the consuming application. */
  closeLabel: string;
  title?: string;
  labelledBy?: string;
  children?: ReactNode;
  className?: string;
  description?: ReactNode;
  /** Controls shown in the header row, between the title and the close button. */
  headerExtra?: ReactNode;
  footer?: ReactNode;
  bodyClassName?: string;
  variant?: "default" | "confirmation";
  dismissible?: boolean;
}

export function Modal({
  open,
  onClose,
  closeLabel,
  title,
  labelledBy,
  children,
  className,
  description,
  headerExtra,
  footer,
  bodyClassName,
  variant = "default",
  dismissible = true,
}: ModalProps) {
  const titleId = useId();
  const descriptionId = useId();
  const confirmation = variant === "confirmation";
  function dismiss() {
    if (dismissible) onClose();
  }
  const closeButton = (
    <button
      type="button"
      onClick={dismiss}
      disabled={!dismissible}
      className="-mr-1.5 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
      aria-label={closeLabel}
    >
      <X className="h-4 w-4" />
    </button>
  );
  return (
    <ModalShell
      open={open}
      onClose={dismiss}
      labelledBy={labelledBy ?? (title ? titleId : undefined)}
      describedBy={description ? descriptionId : undefined}
      className={cn(
        "flex max-h-[calc(100dvh-32px)] w-full flex-col overflow-hidden rounded-lg border border-border-strong bg-card text-card-foreground shadow-dialog",
        confirmation ? "max-w-120" : "max-w-160",
        className,
      )}
    >
      {title ? (
        <div
          className={cn(
            "flex items-start gap-4 px-6",
            confirmation
              ? "min-h-0 overflow-y-auto py-4"
              : "shrink-0 border-b border-border pb-5 pt-6",
          )}
        >
          <div className="min-w-0 flex-1">
            <h2
              id={titleId}
              className="wrap-anywhere text-lg font-semibold leading-snug tracking-tight"
            >
              {title}
            </h2>
            {description && (
              <div
                id={descriptionId}
                className={cn(
                  "max-w-[65ch] wrap-anywhere",
                  confirmation
                    ? "mt-2 text-sm leading-relaxed text-card-foreground"
                    : "mt-1.5 text-field leading-normal text-muted-foreground",
                )}
              >
                {description}
              </div>
            )}
          </div>
          {headerExtra}
          {closeButton}
        </div>
      ) : (
        <div className="absolute right-4 top-4">{closeButton}</div>
      )}
      {(!confirmation || children != null) && (
        <div
          className={cn(
            "min-h-0 overflow-y-auto",
            confirmation ? "px-6 pb-5" : "p-6",
            bodyClassName,
          )}
        >
          {children}
        </div>
      )}
      {footer && (
        <div
          className={cn(
            "flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border px-6",
            confirmation ? "bg-card py-3" : "bg-plate py-4",
          )}
        >
          {footer}
        </div>
      )}
    </ModalShell>
  );
}

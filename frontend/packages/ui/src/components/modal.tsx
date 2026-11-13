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
  children: ReactNode;
  className?: string;
  description?: ReactNode;
  footer?: ReactNode;
  bodyClassName?: string;
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
  footer,
  bodyClassName,
}: ModalProps) {
  const titleId = useId();
  const descriptionId = useId();
  const closeButton = (
    <button
      type="button"
      onClick={onClose}
      className="-mr-1.5 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center text-muted-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-label={closeLabel}
    >
      <X className="h-4 w-4" />
    </button>
  );
  return (
    <ModalShell
      open={open}
      onClose={onClose}
      labelledBy={labelledBy ?? (title ? titleId : undefined)}
      describedBy={description ? descriptionId : undefined}
      className={cn(
        "flex max-h-[calc(100dvh-32px)] w-full max-w-160 flex-col overflow-hidden rounded-lg border border-border-strong bg-card text-card-foreground shadow-dialog",
        className,
      )}
    >
      {title ? (
        <div className="flex shrink-0 items-start gap-4 border-b border-border px-6 pb-5 pt-6">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-lg font-semibold leading-snug tracking-tight">
              {title}
            </h2>
            {description && (
              <div
                id={descriptionId}
                className="mt-1.5 max-w-[65ch] text-[13px] leading-normal text-muted-foreground"
              >
                {description}
              </div>
            )}
          </div>
          {closeButton}
        </div>
      ) : (
        <div className="absolute right-4 top-4">{closeButton}</div>
      )}
      <div className={cn("min-h-0 overflow-y-auto p-6", bodyClassName)}>{children}</div>
      {footer && (
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border bg-plate px-6 py-4">
          {footer}
        </div>
      )}
    </ModalShell>
  );
}

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
        className="absolute inset-0 bg-black/40 backdrop-blur-sm transition-opacity duration-fast ease-out data-[state=closed]:opacity-0"
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        data-state={state}
        className={cn(
          "relative outline-none transition-[opacity,transform] duration-fast ease-out",
          "data-[state=closed]:scale-[0.97] data-[state=closed]:opacity-0 motion-reduce:data-[state=closed]:scale-100",
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
}

export function Modal({
  open,
  onClose,
  closeLabel,
  title,
  labelledBy,
  children,
  className,
}: ModalProps) {
  const titleId = useId();
  const closeButton = (
    <button
      type="button"
      onClick={onClose}
      className="rounded-md p-1 hover:bg-accent"
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
      className={cn("w-full max-w-lg rounded-lg border bg-background p-6 shadow-lg", className)}
    >
      {title ? (
        <div className="mb-4 flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold">
            {title}
          </h2>
          {closeButton}
        </div>
      ) : (
        <div className="absolute right-4 top-4">{closeButton}</div>
      )}
      {children}
    </ModalShell>
  );
}

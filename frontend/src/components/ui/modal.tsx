"use client";

import { ReactNode, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { DURATION, useMountTransition, useOverlayBehavior } from "@/lib/overlay";

/**
 * Low-level dialog chrome: portal, animated backdrop + panel, focus trap,
 * Escape, scroll lock. Use `Modal` for the standard titled dialog; use
 * `ModalShell` directly when a dialog needs fully custom panel markup.
 */
export function ModalShell({
  open = true,
  onClose,
  labelledBy,
  className,
  children,
}: {
  open?: boolean;
  onClose: () => void;
  labelledBy?: string;
  className?: string;
  children: ReactNode;
}) {
  // Must match the panel's `duration-fast` exit transition below.
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

export function Modal({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  const titleId = useId();
  return (
    <ModalShell
      open={open}
      onClose={onClose}
      labelledBy={title ? titleId : undefined}
      className={cn(
        "w-full max-w-lg rounded-lg border bg-background p-6 shadow-lg",
        className,
      )}
    >
      <div className="mb-4 flex items-center justify-between">
        {title ? (
          <h2 id={titleId} className="text-lg font-semibold">
            {title}
          </h2>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 hover:bg-accent"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {children}
    </ModalShell>
  );
}

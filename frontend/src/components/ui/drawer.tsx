"use client";

import { ReactNode, useRef } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";
import { DURATION, useMountTransition, useOverlayBehavior } from "@/lib/overlay";

const SIDE_CLASSES = {
  left: cn(
    "absolute left-0 top-0 bottom-0",
    "transition-transform duration-fast ease-out data-[state=closed]:-translate-x-full",
    "motion-reduce:transition-opacity motion-reduce:data-[state=closed]:translate-x-0 motion-reduce:data-[state=closed]:opacity-0",
  ),
  bottom: cn(
    "absolute inset-x-0 bottom-0",
    "transition-transform duration-fast ease-out data-[state=closed]:translate-y-full",
    "motion-reduce:transition-opacity motion-reduce:data-[state=closed]:translate-y-0 motion-reduce:data-[state=closed]:opacity-0",
  ),
} as const;

export function Drawer({
  open,
  onClose,
  side,
  ariaLabel,
  containerClassName,
  className,
  children,
}: {
  open: boolean;
  onClose: () => void;
  side: keyof typeof SIDE_CLASSES;
  ariaLabel: string;
  containerClassName?: string;
  className?: string;
  children: ReactNode;
}) {
  // Must match the `duration-fast` slide transition in SIDE_CLASSES.
  const { mounted, state } = useMountTransition(open, DURATION.fast);
  const panelRef = useRef<HTMLDivElement>(null);
  useOverlayBehavior(open, onClose, panelRef);

  if (!mounted) return null;
  return createPortal(
    <div className={cn("fixed inset-0 z-overlay", containerClassName)}>
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
        aria-label={ariaLabel}
        data-state={state}
        className={cn(SIDE_CLASSES[side], className)}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

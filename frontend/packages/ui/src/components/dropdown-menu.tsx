"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useRef } from "react";

import { DURATION, useMountTransition } from "../lib/overlay";
import { cn } from "../lib/utils";

export interface DropdownMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
  align?: "start" | "end";
  role?: "menu" | "listbox" | "dialog";
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}

/**
 * Anchored floating panel with outside dismiss, Escape refocus, arrow-key
 * roving, and origin-aware scale/fade entrance and exit.
 */
export function DropdownMenu({
  open,
  onOpenChange,
  trigger,
  align = "end",
  role = "menu",
  className,
  contentClassName,
  children,
}: DropdownMenuProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { mounted, state } = useMountTransition(open, DURATION.press);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      const target = e.target;
      if (target instanceof Node && wrapperRef.current?.contains(target)) return;
      onOpenChange(false);
    }
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open || role === "dialog") return;
    const raf = requestAnimationFrame(() => {
      wrapperRef.current
        ?.querySelector<HTMLElement>(
          '[role="menuitem"], [role="menuitemcheckbox"], [role="option"]',
        )
        ?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [open, role]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onOpenChange(false);
      wrapperRef.current?.querySelector<HTMLElement>("[data-menu-trigger]")?.focus();
      return;
    }
    if (role === "dialog" && e.target instanceof Node) {
      const dialog = wrapperRef.current?.querySelector('[role="dialog"]');
      if (!dialog?.contains(e.target)) return;
      // Nested pickers own their text navigation and must not move the parent
      // menu's roving focus while a user types or navigates within them.
      e.stopPropagation();
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = Array.from(
      wrapperRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"], [role="menuitemcheckbox"], [role="option"]',
      ) ?? [],
    );
    if (items.length === 0) return;
    e.preventDefault();
    e.stopPropagation();
    const current = items.findIndex((item) => item === document.activeElement);
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? items.length - 1
          : e.key === "ArrowDown"
            ? (current + 1) % items.length
            : (current - 1 + items.length) % items.length;
    items[next]?.focus();
  }

  return (
    <div ref={wrapperRef} className={cn("relative", className)} onKeyDown={onKeyDown}>
      {trigger}
      {mounted && (
        <div
          role={role}
          data-state={state}
          className={cn(
            "absolute top-full z-dropdown mt-2",
            align === "end" ? "right-0 origin-top-right" : "left-0 origin-top-left",
            "transition-[opacity,transform] duration-press ease-out",
            "data-[state=closed]:opacity-0 data-[state=closed]:scale-95 motion-reduce:data-[state=closed]:scale-100",
            contentClassName,
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}

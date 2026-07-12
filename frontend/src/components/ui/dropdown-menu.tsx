"use client";

import { ReactNode, useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import { DURATION, useMountTransition } from "@/lib/overlay";

/**
 * Anchored floating panel: trigger + content in a relative wrapper.
 * Handles outside-pointerdown dismiss, Escape (refocuses the trigger),
 * arrow-key roving over [role=menuitem]/[role=option] children, and an
 * origin-aware scale+fade entrance/exit.
 *
 * The trigger element must carry `data-menu-trigger`, `aria-haspopup`,
 * and `aria-expanded` (see migrations in plan 004 for the pattern).
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
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
  align?: "start" | "end";
  role?: "menu" | "listbox" | "dialog";
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  // Must match the content's `duration-press` exit transition below.
  const { mounted, state } = useMountTransition(open, DURATION.press);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!wrapperRef.current?.contains(e.target as Node)) onOpenChange(false);
    }
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [open, onOpenChange]);

  // Menus move focus to their first item so arrow keys work immediately.
  useEffect(() => {
    if (!open || role === "dialog") return;
    const raf = requestAnimationFrame(() => {
      wrapperRef.current
        ?.querySelector<HTMLElement>('[role="menuitem"], [role="option"]')
        ?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [open, role]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onOpenChange(false);
      wrapperRef.current
        ?.querySelector<HTMLElement>("[data-menu-trigger]")
        ?.focus();
      return;
    }
    if (role === "dialog") return;
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = Array.from(
      wrapperRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"], [role="option"]',
      ) ?? [],
    );
    if (items.length === 0) return;
    e.preventDefault();
    const current = items.indexOf(document.activeElement as HTMLElement);
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

"use client";

import { ReactNode, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { cn } from "@/lib/utils";

export type TabItem<K extends string = string> = { key: K; label: ReactNode };

/**
 * ARIA tablist with roving tabindex and a sliding active-underline. The
 * indicator is absolutely positioned (affects no other layout) and moves
 * with translateX + width, retargeting smoothly on rapid tab changes.
 */
export function TabBar<K extends string>({
  tabs,
  active,
  onChange,
  className,
  tabClassName,
  activeTabClassName,
  indicatorInset = 0,
  showIndicator = true,
}: {
  tabs: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  className?: string;
  tabClassName?: string;
  activeTabClassName?: string;
  indicatorInset?: number;
  /** Disable when the active tab already has a filled selected state. */
  showIndicator?: boolean;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      const el = list.querySelector<HTMLElement>('[data-active="true"]');
      if (el) setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
      else setIndicator(null);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    return () => ro.disconnect();
  }, [active, tabs.length]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const idx = tabs.findIndex((t) => t.key === active);
    const next =
      e.key === "ArrowRight"
        ? (idx + 1) % tabs.length
        : (idx - 1 + tabs.length) % tabs.length;
    onChange(tabs[next].key);
    requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLElement>('[data-active="true"]')?.focus();
    });
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      onKeyDown={onKeyDown}
      className={cn("relative flex", className)}
    >
      {tabs.map((tab) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            data-active={isActive ? "true" : undefined}
            onClick={() => onChange(tab.key)}
            className={cn(tabClassName, isActive && activeTabClassName)}
          >
            {tab.label}
          </button>
        );
      })}
      {showIndicator && indicator && (
        <span
          aria-hidden
          className="absolute bottom-0 left-0 h-0.5 w-px origin-left rounded-full bg-primary transition-transform duration-fast ease-in-out motion-reduce:transition-none"
          style={{
            transform: `translateX(${indicator.left + indicatorInset}px) scaleX(${Math.max(0, indicator.width - indicatorInset * 2)})`,
          }}
        />
      )}
    </div>
  );
}

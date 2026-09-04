"use client";

import { type KeyboardEvent, type ReactNode, useLayoutEffect, useRef, useState } from "react";

import { cn } from "../lib/utils";

export type TabItem<K extends string = string> = { key: K; label: ReactNode };

/**
 * Feature detection: ResizeObserver is absent during SSR and in bare Node test
 * environments, where the indicator simply stays at its measured position.
 */
const canObserveResize = "ResizeObserver" in globalThis;

export interface TabBarProps<K extends string> {
  tabs: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  className?: string;
  tabClassName?: string;
  activeTabClassName?: string;
  indicatorInset?: number;
  /** Disable when the active tab already has a filled selected state. */
  showIndicator?: boolean;
}

/** ARIA tablist with roving tabindex and a sliding active underline. */
export function TabBar<K extends string>({
  tabs,
  active,
  onChange,
  className,
  tabClassName,
  activeTabClassName,
  indicatorInset = 0,
  showIndicator = true,
}: TabBarProps<K>) {
  const listRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{
    left: number;
    width: number;
  } | null>(null);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      const el = list.querySelector<HTMLElement>('[data-active="true"]');
      if (el) setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
      else setIndicator(null);
    };
    measure();
    if (!canObserveResize) return;
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    return () => ro.disconnect();
  }, [active, tabs.length]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const idx = tabs.findIndex((tab) => tab.key === active);
    const next =
      e.key === "ArrowRight" ? (idx + 1) % tabs.length : (idx - 1 + tabs.length) % tabs.length;
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

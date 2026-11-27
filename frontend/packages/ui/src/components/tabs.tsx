"use client";

import { type KeyboardEvent, type ReactNode, useLayoutEffect, useRef, useState } from "react";

import { cn } from "../lib/utils";

export type TabItem<K extends string = string> = { key: K; label: ReactNode };

/**
 * Feature detection: ResizeObserver is absent during SSR and in bare Node test
 * environments, where the indicator simply stays at its measured position.
 */
const canObserveResize = "ResizeObserver" in globalThis;

/**
 * The one tab look: a bordered inset frame with a neutral `accent` fill on the
 * active tab (DESIGN.md "Framed work surfaces"). Pass `variant="framed"` and
 * add only layout classes (`w-full`, `self-start`, `overflow-x-auto`).
 */
const TAB_FRAME_CLASS =
  "relative inline-flex max-w-full gap-0.5 overflow-x-auto rounded-lg border border-border bg-card p-0.75";
const TAB_CLASS =
  "inline-flex h-7.5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-sm font-medium text-muted-foreground transition-[color,background-color] duration-press hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const TAB_ACTIVE_CLASS = "bg-accent text-accent-foreground hover:text-accent-foreground";

export interface TabBarProps<K extends string> {
  variant?: "framed";
  tabs: TabItem<K>[];
  active: K | null;
  onChange: (key: K) => void;
  className?: string;
  tabClassName?: string;
  activeTabClassName?: string;
  indicatorInset?: number;
  /** Disable when the active tab already has a filled selected state. */
  showIndicator?: boolean;
}

/** ARIA tablist with roving tabindex and a measured active underline. */
export function TabBar<K extends string>({
  variant,
  tabs,
  active,
  onChange,
  className,
  tabClassName,
  activeTabClassName,
  indicatorInset = 0,
  showIndicator = true,
}: TabBarProps<K>) {
  const framed = variant === "framed";
  const listRef = useRef<HTMLDivElement>(null);
  const [moreAfter, setMoreAfter] = useState(false);
  const [indicator, setIndicator] = useState<{
    left: number;
    width: number;
  } | null>(null);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    // A strip wider than its box fades at the cut edge so clipped tabs read as "more".
    const measureOverflow = () =>
      setMoreAfter(list.scrollWidth - list.clientWidth - list.scrollLeft > 2);
    list.addEventListener("scroll", measureOverflow, { passive: true });
    const stopScroll = () => list.removeEventListener("scroll", measureOverflow);
    const measure = () => {
      measureOverflow();
      const el = list.querySelector<HTMLElement>('[data-active="true"]');
      if (el) setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
      else setIndicator(null);
    };
    if (!canObserveResize) {
      measure();
      return stopScroll;
    }
    // Initial observation arrives after layout and before paint. Measuring in
    // this layout effect forced the whole library to lay out during commit.
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    return () => {
      stopScroll();
      ro.disconnect();
    };
  }, [active, tabs.length]);

  const activeIndex = tabs.findIndex((tab) => tab.key === active);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    if (buttons.length === 0) return;
    e.preventDefault();
    const focused = buttons.findIndex((button) => button === document.activeElement);
    const index = focused >= 0 ? focused : activeIndex;
    const next =
      e.key === "ArrowRight"
        ? (index + 1) % tabs.length
        : index < 0
          ? tabs.length - 1
          : (index - 1 + tabs.length) % tabs.length;
    // Route-backed selection can commit later. Move focus to the requested
    // button now, rather than reading the old active tab on the next frame.
    buttons[next].focus();
    onChange(tabs[next].key);
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      onKeyDown={onKeyDown}
      data-more-after={moreAfter ? "true" : undefined}
      className={cn(
        framed ? TAB_FRAME_CLASS : "relative flex",
        moreAfter && "[mask-image:linear-gradient(to_right,black_calc(100%-28px),transparent)]",
        className,
      )}
    >
      {tabs.map((tab, index) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive || (activeIndex < 0 && index === 0) ? 0 : -1}
            data-active={isActive ? "true" : undefined}
            onClick={() => onChange(tab.key)}
            className={cn(
              framed && TAB_CLASS,
              tabClassName,
              isActive && (framed ? TAB_ACTIVE_CLASS : null),
              isActive && activeTabClassName,
            )}
          >
            {tab.label}
          </button>
        );
      })}
      {showIndicator && !framed && indicator && (
        <span
          aria-hidden
          className="absolute bottom-0 left-0 h-0.5 w-px origin-left bg-foreground"
          style={{
            transform: `translateX(${indicator.left + indicatorInset}px) scaleX(${Math.max(0, indicator.width - indicatorInset * 2)})`,
          }}
        />
      )}
    </div>
  );
}

import { type RefObject, useEffect, useRef, useState } from "react";

/** The --duration-* scale from styles.css, in milliseconds. */
export const DURATION = { press: 150, fast: 200, slow: 300 } as const;

/**
 * Keeps an overlay mounted during its exit transition. `state` drives
 * data-state CSS; opening is deferred so the browser paints the closed styles.
 *
 * Both outputs are derived from `open` during render, so an overlay mounts in
 * the same commit that opens it. The two pieces of state are the transitions
 * themselves — the frame the enter styles land on, and the exit window that
 * outlives `open` — and only their timer callbacks write them.
 */
export function useMountTransition(open: boolean, exitMs: number) {
  const [entered, setEntered] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [previousOpen, setPreviousOpen] = useState(open);

  if (previousOpen !== open) {
    setPreviousOpen(open);
    // Re-arm the enter transition on open; hold the panel in the tree while the
    // exit transition plays on close.
    setEntered(false);
    setExiting(!open);
  }

  useEffect(() => {
    if (!open) return;
    const raf = requestAnimationFrame(() => requestAnimationFrame(() => setEntered(true)));
    return () => cancelAnimationFrame(raf);
  }, [open]);

  useEffect(() => {
    if (open || !exiting) return;
    const timer = window.setTimeout(() => setExiting(false), exitMs);
    return () => window.clearTimeout(timer);
  }, [open, exiting, exitMs]);

  return { mounted: open || exiting, state: open && entered ? "open" : "closed" } as const;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Shared dialog behavior: Escape closes, Tab is trapped inside the panel,
 * body scroll locks, and focus returns to the opener on close.
 */
export function useOverlayBehavior(
  open: boolean,
  onClose: () => void,
  panelRef: RefObject<HTMLElement | null>,
) {
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement;
    const panel = panelRef.current;
    const initial = panel?.querySelector<HTMLElement>("[autofocus]") ?? panel;
    initial?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = panel?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!items || items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }

    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      if (restoreTo instanceof HTMLElement) restoreTo.focus();
    };
  }, [open, panelRef]);
}

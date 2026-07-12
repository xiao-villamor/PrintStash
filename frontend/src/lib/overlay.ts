import { RefObject, useEffect, useRef, useState } from "react";

/**
 * The --duration-* scale from globals.css, in milliseconds.
 *
 * An overlay's unmount timer and its CSS exit transition are two halves of one
 * animation, but they live in different languages and nothing type-checks them
 * against each other. Hand-typing the millisecond count on the JS side is how
 * they drift: a timer shorter than the transition tears the panel out of the
 * DOM mid-animation and the close visibly snaps. Pass one of these and match it
 * to the `duration-*` class the panel actually uses.
 */
export const DURATION = { press: 150, fast: 200, slow: 300 } as const;

/**
 * Keeps an overlay mounted during its exit transition. `state` drives
 * data-state CSS; flipping to "open" is deferred two frames so the browser
 * paints the closed styles first and the entrance transition actually runs.
 */
export function useMountTransition(open: boolean, exitMs: number) {
  const [mounted, setMounted] = useState(open);
  const [state, setState] = useState<"open" | "closed">("closed");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (open) {
      window.clearTimeout(timer.current);
      setMounted(true);
      const raf = requestAnimationFrame(() =>
        requestAnimationFrame(() => setState("open")),
      );
      return () => cancelAnimationFrame(raf);
    }
    setState("closed");
    timer.current = window.setTimeout(() => setMounted(false), exitMs);
    return () => window.clearTimeout(timer.current);
  }, [open, exitMs]);

  return { mounted, state };
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
  // Callers pass an inline onClose, so a parent re-render would otherwise
  // re-run this effect — stealing focus back to the panel mid-typing.
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement as HTMLElement | null;
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
      restoreTo?.focus();
    };
  }, [open, panelRef]);
}

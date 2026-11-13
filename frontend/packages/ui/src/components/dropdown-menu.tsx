"use client";

import {
  createContext,
  type AriaAttributes,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { DURATION, useMountTransition } from "../lib/overlay";
import { cn } from "../lib/utils";

const GUTTER = 8;
const GAP = 8;
const ITEMS =
  ':is([role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], [role="option"]):not(:disabled):not([aria-disabled="true"])';
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

type DropdownScope = {
  register: (panel: HTMLElement) => () => void;
  portalHost: HTMLElement | null;
};
const DropdownContext = createContext<DropdownScope | null>(null);

export interface DropdownMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
  /** Context menus may open from several controls in a shared trigger surface. */
  returnFocusRef?: RefObject<HTMLElement | null>;
  align?: "start" | "end";
  role?: "menu" | "listbox" | "dialog";
  "aria-label"?: AriaAttributes["aria-label"];
  /** Escape a clipping ancestor while retaining the enclosing modal layer. */
  portal?: boolean;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}

/** Anchored panel with viewport collision handling and shared keyboard ownership. */
export function DropdownMenu({
  open,
  onOpenChange,
  trigger,
  returnFocusRef,
  align = "end",
  role = "menu",
  "aria-label": ariaLabel,
  portal = false,
  className,
  contentClassName,
  children,
}: DropdownMenuProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const descendants = useRef(new Set<HTMLElement>());
  const parent = useContext(DropdownContext);
  const [portalHost, setPortalHost] = useState<HTMLElement | null>(null);
  const { mounted, state } = useMountTransition(open, DURATION.press);
  const scope = useMemo<DropdownScope>(
    () => ({
      portalHost,
      register(panel) {
        descendants.current.add(panel);
        const unregister = parent?.register(panel);
        return () => {
          descendants.current.delete(panel);
          unregister?.();
        };
      },
    }),
    [parent, portalHost],
  );

  useLayoutEffect(() => {
    if (!mounted || !portal) return;
    const modal = wrapperRef.current?.closest('[aria-modal="true"]');
    setPortalHost(modal?.parentElement ?? parent?.portalHost ?? document.body);
  }, [mounted, portal, parent?.portalHost]);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (mounted && panel) return parent?.register(panel);
  }, [mounted, parent, portalHost]);

  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;
    const panel = panelRef.current;
    if (!mounted || !wrapper || !panel) return;
    const configuredMaxHeight = getComputedStyle(panel).maxHeight;
    function position() {
      if (!wrapper || !panel) return;
      const anchor = wrapper.getBoundingClientRect();
      const viewport = window.visualViewport;
      const leftEdge = (viewport?.offsetLeft ?? 0) + GUTTER;
      const topEdge = (viewport?.offsetTop ?? 0) + GUTTER;
      const rightEdge = leftEdge + (viewport?.width ?? window.innerWidth) - GUTTER * 2;
      const bottomEdge = topEdge + (viewport?.height ?? window.innerHeight) - GUTTER * 2;
      panel.style.maxWidth = Math.max(0, rightEdge - leftEdge) + "px";
      const width = panel.offsetWidth;
      const naturalHeight = panel.scrollHeight + panel.offsetHeight - panel.clientHeight;
      const limit = Number.parseFloat(configuredMaxHeight);
      const height = Number.isFinite(limit) ? Math.min(naturalHeight, limit) : naturalHeight;
      const preferredLeft = align === "end" ? anchor.right - width : anchor.left;
      const left = Math.max(leftEdge, Math.min(preferredLeft, rightEdge - width));
      const below = Math.max(0, bottomEdge - anchor.bottom - GAP);
      const above = Math.max(0, anchor.top - GAP - topEdge);
      const upward = height > below && above > below;
      const available = upward ? above : below;
      const top = upward
        ? Math.max(topEdge, anchor.top - GAP - Math.min(height, available))
        : Math.max(topEdge, Math.min(anchor.bottom + GAP, bottomEdge));
      panel.style.left = left - (portal ? 0 : anchor.left + wrapper.clientLeft) + "px";
      panel.style.top = top - (portal ? 0 : anchor.top + wrapper.clientTop) + "px";
      panel.style.right = "auto";
      panel.style.marginTop = "0";
      panel.style.maxHeight = Number.isFinite(limit)
        ? Math.min(available, limit) + "px"
        : available + "px";
      panel.style.overflowY = height > available ? "auto" : "";
      panel.style.transformOrigin =
        (align === "end" ? "right" : "left") + (upward ? " bottom" : " top");
    }
    function onResize() {
      // A responsive toolbar can disappear while its portaled menu is open.
      // Check CSS visibility, not layout size: a zero rect is also valid during
      // initial layout and in non-layout renderers such as jsdom.
      for (let node: HTMLElement | null = wrapper; node; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (node.hidden || style.display === "none" || style.visibility === "hidden") {
          onOpenChange(false);
          return;
        }
      }
      position();
    }
    position();
    // A panel can change size when its available height changes. Defer the
    // observer's style writes so they cannot invalidate its own delivery frame.
    let resizeFrame = 0;
    const observer =
      "ResizeObserver" in globalThis
        ? new ResizeObserver(() => {
            cancelAnimationFrame(resizeFrame);
            resizeFrame = requestAnimationFrame(onResize);
          })
        : null;
    observer?.observe(wrapper);
    observer?.observe(panel);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", position, true);
    window.visualViewport?.addEventListener("resize", onResize);
    window.visualViewport?.addEventListener("scroll", position);
    return () => {
      observer?.disconnect();
      cancelAnimationFrame(resizeFrame);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", position, true);
      window.visualViewport?.removeEventListener("resize", onResize);
      window.visualViewport?.removeEventListener("scroll", position);
    };
  }, [mounted, align, portal, portalHost, onOpenChange]);

  function contains(target: Node) {
    return (
      wrapperRef.current?.contains(target) ||
      panelRef.current?.contains(target) ||
      [...descendants.current].some((panel) => panel.contains(target))
    );
  }

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (e.target instanceof Node && contains(e.target)) return;
      onOpenChange(false);
    }
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open || role === "dialog") return;
    const raf = requestAnimationFrame(() =>
      panelRef.current?.querySelector<HTMLElement>(ITEMS)?.focus(),
    );
    return () => cancelAnimationFrame(raf);
  }, [open, role]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    // Portaled dialogs can bubble through a card's closed contextual menu.
    // Only an open panel owns Escape or roving focus; otherwise its child
    // dialog and the page's keyboard commands must receive the event.
    if (!open) return;
    const triggerElement =
      returnFocusRef?.current ??
      wrapperRef.current?.querySelector<HTMLElement>("[data-menu-trigger]");
    if (e.key === "Escape") {
      e.stopPropagation();
      onOpenChange(false);
      triggerElement?.focus();
      return;
    }
    if (portal && e.key === "Tab" && triggerElement) {
      const fields = [...(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])];
      const first = fields[0];
      const last = fields.at(-1);
      if (!e.shiftKey && e.target === triggerElement && first) {
        e.preventDefault();
        e.stopPropagation();
        first.focus();
        return;
      }
      if ((e.shiftKey && e.target === first) || (!e.shiftKey && e.target === last)) {
        e.stopPropagation();
        onOpenChange(false);
        triggerElement.focus();
        if (e.shiftKey) e.preventDefault();
        else {
          const modal = triggerElement.closest<HTMLElement>('[aria-modal="true"]');
          const controls = [
            ...(modal ?? document.body).querySelectorAll<HTMLElement>(FOCUSABLE),
          ].filter((element) => {
            if (panelRef.current?.contains(element)) return false;
            if ([...descendants.current].some((panel) => panel.contains(element))) return false;
            for (let node: HTMLElement | null = element; node; node = node.parentElement) {
              const style = getComputedStyle(node);
              if (
                node.hidden ||
                node.inert ||
                style.display === "none" ||
                style.visibility === "hidden"
              )
                return false;
            }
            return element.tabIndex >= 0;
          });
          const next =
            controls[controls.indexOf(triggerElement) + 1] ?? (modal ? controls[0] : null);
          if (next) {
            e.preventDefault();
            next.focus();
          }
        }
        return;
      }
    }
    if (role === "dialog" && e.target instanceof Node) {
      if (!panelRef.current?.contains(e.target)) return;
      e.stopPropagation();
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(ITEMS) ?? []);
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

  const panel = mounted && (
    <div
      ref={panelRef}
      role={role}
      aria-label={ariaLabel}
      data-state={state}
      className={cn(
        portal ? "fixed z-dropdown" : "absolute top-full z-dropdown mt-2",
        align === "end" ? "right-0 origin-top-right" : "left-0 origin-top-left",
        "transition-[opacity,transform] duration-press ease-out",
        "data-[state=closed]:opacity-0 data-[state=closed]:scale-95 motion-reduce:data-[state=closed]:scale-100",
        contentClassName,
      )}
    >
      {children}
    </div>
  );
  return (
    <DropdownContext.Provider value={scope}>
      <div ref={wrapperRef} className={cn("relative", className)} onKeyDown={onKeyDown}>
        {trigger}
        {portal ? portalHost && createPortal(panel, portalHost) : panel}
      </div>
    </DropdownContext.Provider>
  );
}

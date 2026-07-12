# 003 — One overlay system: animated, focus-trapped Modal + Drawer primitives

- **Status**: DONE
- **Commit**: 57770ff
- **Depends on**: 001 (motion tokens, `duration-fast`/`ease-out` utilities), 002 (`z-overlay` token, M3 class codemod)
- **Severity**: HIGH
- **Category**: Interruptibility / Missed opportunities / Component architecture / Accessibility
- **Estimated scope**: 2 new files, 1 rewritten primitive, 8 migrated components

## Problem

Eleven `fixed inset-0` overlays exist; only `ConfirmModal` uses the `ui/Modal` primitive. Consequences:

1. **No overlay animates its exit, and most don't animate entry.** The three drawers animate IN via one-shot keyframes then vanish instantly (`if (!open) return null`):

```tsx
/* frontend/src/components/mobile-nav-drawer.tsx:42-50 — current */
  if (!open) return null;

  return (
    <div className="md:hidden fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="absolute left-0 top-0 bottom-0 w-[280px] max-w-[85vw] bg-[var(--surface-container-low)] shadow-xl slide-in-left flex flex-col">
```

Dialogs pop in with zero motion (`frontend/src/components/upload-modal.tsx:821-833`, `share-dialog.tsx:106-112`, `add-revision-modal.tsx:70-74`, `taxonomy-manager.tsx:612-614`, `printers-list.tsx:238-243`, and `ui/modal.tsx` itself).

2. **Accessibility is inconsistent**: `ui/modal.tsx` has Escape + scroll lock but **no portal, no focus trap, no focus restore, no `aria-labelledby`**. `share-dialog.tsx` has no Escape at all. The drawers have no `role="dialog"`. `upload-modal.tsx:209-220` re-implements its own Escape listener.

3. **Z-index conflict**: bespoke dialogs use `z-50`, `ui/modal.tsx` uses `z-[100]` — a bespoke dialog can render under a Modal-based one.

## Target

Two primitives sharing one behavior layer:

- `ModalShell` — portal, animated backdrop + panel (scale 0.97→1 + fade, `--duration-fast` `--ease-out`, transition-based so it retargets if toggled mid-motion), focus trap, focus restore, Escape, scroll lock, `z-overlay`.
- `Modal` — `ModalShell` + the existing titled-header API (unchanged call signature).
- `Drawer` — same behavior layer, slides from `left`/`bottom` with symmetric enter/exit.
- Reduced motion: movement drops (no slide/scale), opacity fade remains.

All 8 bespoke overlays migrate onto them. Exit animations work wherever the component already receives an `open` prop (all drawers, upload-modal, share-dialog); overlays that parents conditionally mount keep instant exit — acceptable for occasional dialogs.

## Repo conventions to follow

- Primitives live in `frontend/src/components/ui/`, hooks in `frontend/src/lib/` (see `use-media-query.ts`).
- `cn()` from `@/lib/utils` for class merging; props typed inline (see current `ui/modal.tsx:7-19`).
- Class conventions after plan 002: token classes (`bg-surface-container-lowest`, `border-outline-variant`), no `[var(--…)]`.

## Steps

### Step 1 — New file `frontend/src/lib/overlay.ts` (complete listing)

```ts
import { RefObject, useEffect, useRef, useState } from "react";

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
  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const initial = panel?.querySelector<HTMLElement>("[autofocus]") ?? panel;
    initial?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
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
  }, [open, onClose, panelRef]);
}
```

### Step 2 — Rewrite `frontend/src/components/ui/modal.tsx` (complete listing)

```tsx
"use client";

import { ReactNode, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMountTransition, useOverlayBehavior } from "@/lib/overlay";

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
  const { mounted, state } = useMountTransition(open, 150);
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
```

(Public `Modal` API is unchanged; `ConfirmModal` and the 5 existing `Modal` consumers need no edits. The backdrop changes from `bg-background/80` to `bg-black/40 backdrop-blur-sm` — the convention every bespoke dialog already uses.)

### Step 3 — New file `frontend/src/components/ui/drawer.tsx` (complete listing)

```tsx
"use client";

import { ReactNode, useRef } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";
import { useMountTransition, useOverlayBehavior } from "@/lib/overlay";

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
  const { mounted, state } = useMountTransition(open, 200);
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
```

### Step 4 — Migrate the three drawers

**`frontend/src/components/mobile-nav-drawer.tsx`**: delete the scroll-lock `useEffect` (lines 31-40), delete `if (!open) return null;` (line 42), and replace the outer two `<div>`s (lines 45-50):

```tsx
/* before (lines 44-50) */
  return (
    <div className="md:hidden fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="absolute left-0 top-0 bottom-0 w-[280px] max-w-[85vw] bg-surface-container-low shadow-xl slide-in-left flex flex-col">

/* after */
  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="left"
      ariaLabel="Navigation"
      containerClassName="md:hidden"
      className="w-[280px] max-w-[85vw] bg-surface-container-low shadow-xl flex flex-col"
    >
```

…and the matching two closing `</div>`s at the end become `</Drawer>`. Import `{ Drawer } from "@/components/ui/drawer"`. Remove the now-unused `useEffect` import if nothing else uses it. Note the `slide-in-left` class is dropped (the Drawer transition replaces it).

**`frontend/src/components/mobile-filter-drawer.tsx`**: identical transformation (scroll-lock effect at lines 34-43, guard at line 45, wrappers at lines 48-56; panel classes `w-[280px] max-w-[85vw] bg-background shadow-xl`, `ariaLabel="Filters"`). It declares `drawerRef` — grep the file for other uses of `drawerRef`; if it is only attached to the panel div, delete it, otherwise STOP and report.

**`frontend/src/components/bottom-nav-bar.tsx` (`MoreSheet`, lines 182-201)**: `MoreSheet` currently has no `open` prop — it is conditionally rendered by its parent. Find the render site (`grep -n "MoreSheet" frontend/src/components/bottom-nav-bar.tsx`), change it from `{moreOpen && <MoreSheet … />}` to `<MoreSheet open={moreOpen} … />`, add `open: boolean` to its props, and replace the wrappers:

```tsx
/* before (lines 195-201) */
  return (
    <div className="md:hidden fixed inset-0 z-50">
      <div
        className="fade-in absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="slide-up absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-border bg-card px-4 pt-3 pb-safe shadow-2xl">

/* after */
  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="bottom"
      ariaLabel="More"
      containerClassName="md:hidden"
      className="rounded-t-2xl border-t border-border bg-card px-4 pt-3 pb-safe shadow-2xl"
    >
```

### Step 5 — Migrate the five bespoke dialogs onto `ModalShell`

Pattern (shown for `frontend/src/components/model-detail/share-dialog.tsx`; apply the same shape to the others):

1. Delete the early-return `if (!open) return null;` (line 56) so exit animation can play (share-dialog receives `open`).
2. Replace the wrappers (lines 106-112):

```tsx
/* before */
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-smm" onClick={onClose} aria-hidden />
      <div
        className="relative bg-surface-container-lowest border border-outline-variant rounded-md w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
        role="dialog"
        aria-modal="true"
      >

/* after */
    <ModalShell
      open={open}
      onClose={onClose}
      className="bg-surface-container-lowest border border-outline-variant rounded-md w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
    >
```

(the `w-full` stays because ModalShell's panel is inside a flex-centered container; `relative`/`role`/`aria-modal` come from the shell). Closing tags collapse accordingly. Import `{ ModalShell } from "@/components/ui/modal"`.

Apply to:

- `frontend/src/components/upload-modal.tsx` (wrappers at 821-833). Also **delete its bespoke Escape effect (lines 209-220)** — the shell handles Escape. The effect called `reset(); onClose();` — make sure the function passed as `onClose` to ModalShell is the component's existing `close` handler that the backdrop already used (line 823), which performs the same reset-then-close. If `close` does not call `reset()`, STOP and report. Keep the component's `open`-prop plumbing (pass `open={open}`).
- `frontend/src/components/model-detail/add-revision-modal.tsx` (wrappers at 70-74). The panel is a `<form>`: move the visual classes (`w-full max-w-md rounded border border-outline-variant bg-surface-container-lowest p-5 shadow-lg`) onto `ModalShell`'s `className` and leave `<form onSubmit={submit} className="space-y-4">` as the shell's child. This component has no `open` prop — omit the `open` attribute (defaults to `true`; entrance animates, exit is parent-unmount).
- `frontend/src/components/taxonomy-manager.tsx` (`PermissionsModal`, wrappers at 612-614; panel classes `w-full max-w-lg rounded bg-card border border-border shadow-xl`). No `open` prop — omit.
- `frontend/src/components/printers-list.tsx` (`AddPrinterModal`, wrappers at 238-244; panel classes `bg-card border border-border rounded w-full max-w-md p-6 shadow-lg`). No `open` prop — omit.

## Boundaries

- Do NOT change any dialog's inner content, form logic, or close-handler semantics — only the overlay chrome.
- Do NOT migrate `notifications-panel.tsx`, `top-bar.tsx` popovers, or comboboxes — those are plan 004.
- Do NOT add new dependencies (no Radix Dialog; the point is hardening the existing hand-rolled layer).
- Do NOT remove the `slide-in-left`/`slide-up`/`fade-in` utilities from `globals.css` even once unused — plan 007 may reuse them; leave cleanup for later.
- If any "before" excerpt doesn't match the file (drift), STOP and report.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` && `pnpm --dir frontend lint` && `pnpm --dir frontend test` pass. `grep -rn "fixed inset-0" frontend/src/components --include="*.tsx"` — remaining hits should only be: `ui/modal.tsx`, `ui/drawer.tsx`, the combobox click-catchers in `upload-modal.tsx`/`overview-tab.tsx` (plan 004's scope), and `setup-gate.tsx`/full-page washes if any.
- **Feel check** (`pnpm --dir frontend dev`):
  - Open and close the upload modal: panel scales 0.97→1 with fade in ~200ms, and scales back down on close — no hard pop in either direction.
  - Narrow viewport: open the nav drawer, then tap the backdrop *while it is still sliding in* — it must reverse smoothly from wherever it is (transitions retarget; keyframes would snap).
  - Tab repeatedly inside the share dialog: focus cycles within the dialog and never reaches the page behind; close it and focus returns to the button that opened it; Escape closes it (it previously didn't).
  - DevTools → Rendering → `prefers-reduced-motion: reduce`: drawers fade instead of sliding; modal doesn't scale.
- **Done when**: every overlay in the app animates open and (where it owns an `open` prop) closed, traps focus, closes on Escape, and sits at `z-overlay`.

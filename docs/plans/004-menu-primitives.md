# 004 — Dropdown/Popover primitive + accessible comboboxes

- **Status**: DONE
- **Commit**: 57770ff
- **Depends on**: 001 (motion tokens), 002 (`z-dropdown` token, M3 class codemod), 003 (`useMountTransition` in `frontend/src/lib/overlay.ts`)
- **Severity**: HIGH
- **Category**: Component architecture / Physicality & origin / Accessibility
- **Estimated scope**: 2 new files, 1 CSS utility, 6 migrated components (8 menus/popovers + 3 chip editors)

## Problem

Eight hand-rolled floating panels across 5 files, each re-implementing open-state, outside-click and positioning differently, none animating from their trigger, and none keyboard-navigable:

```tsx
/* frontend/src/components/top-bar.tsx:125-133 — current: shared mousedown effect for two menus */
  useEffect(() => {
    if (!tasksOpen && !profileOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!tasksRef.current?.contains(event.target as Node)) setTasksOpen(false);
      if (!profileRef.current?.contains(event.target as Node)) setProfileOpen(false);
    }
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [tasksOpen, profileOpen]);
```

- `top-bar.tsx:255` ProfileMenu has `role="menu"`/`menuitem` but no arrow keys, no Escape, no focus return; `top-bar.tsx:301` TaskPopover has no role at all. Both appear instantly with no origin motion.
- `slicer-open-button.tsx:57-66` re-implements the same mousedown-outside effect; its menu (`:103`) has no roles or keyboard support.
- The three chip editors (`batch-toolbar.tsx:273` `ChipEditor`, `upload-modal.tsx` tag input at `:1158-1170`, `overview-tab.tsx` tag editor at `:168-176`) duplicate Enter-to-commit/Backspace-to-remove logic and render suggestion lists as plain divs of buttons — no `role="listbox"`/`option`, no arrow-key navigation, no `aria-activedescendant`.
- The two category pickers (`upload-modal.tsx:1054-1062`, `overview-tab.tsx:93-100`) use invisible `fixed inset-0` click-catcher divs and pop in with no animation.
- Z-indexes disagree (`z-20/z-30` in upload-modal vs `z-40/z-50` in overview-tab vs `z-50` elsewhere).

## Target

- One `DropdownMenu` primitive: outside-`pointerdown` dismiss, Escape-closes-and-refocuses-trigger, ArrowUp/Down/Home/End roving focus over `menuitem`/`option` children, `z-dropdown`, and origin-aware entrance (scale 0.95 + fade from the trigger corner, 150ms `--ease-out`, transition-based exit).
- One `useComboboxNav` hook giving every suggestion list combobox ARIA (`role`, `aria-expanded`, `aria-controls`, `aria-activedescendant`) and arrow-key highlight.
- A shared `.pop-in` utility for conditionally-rendered floating panels that can't use the primitive.

## Repo conventions to follow

- Primitives in `frontend/src/components/ui/`, hooks in `frontend/src/lib/`.
- After 002, all classes are token classes (`bg-popover`, `border-outline-variant`, `text-primary`…). After 001, `duration-press`/`ease-out` utilities exist.
- Menus in this app are small absolutely-positioned panels anchored `top-full` right/left of a `relative` wrapper — the primitive keeps that model (no floating-ui dependency).

## Steps

### Step 1 — New file `frontend/src/components/ui/dropdown-menu.tsx` (complete listing)

```tsx
"use client";

import { ReactNode, useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import { useMountTransition } from "@/lib/overlay";

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
  const { mounted, state } = useMountTransition(open, 150);

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
```

### Step 2 — New file `frontend/src/lib/use-combobox-nav.ts` (complete listing)

```ts
import { useEffect, useId, useState } from "react";
import type { KeyboardEvent } from "react";

/**
 * Keyboard + ARIA wiring for an input with a suggestion list. The caller
 * renders the list (role="listbox", id={listboxId}) and each item
 * (role="option", id={optionId(i)}, aria-selected={i === activeIndex}),
 * and highlights the active item.
 */
export function useComboboxNav(
  itemCount: number,
  handlers: {
    onSelect: (index: number) => void;
    onCommitInput?: () => void;
    onClose?: () => void;
  },
) {
  const [activeIndex, setActiveIndex] = useState(-1);
  const listboxId = useId();

  useEffect(() => {
    if (activeIndex >= itemCount) setActiveIndex(itemCount - 1);
  }, [itemCount, activeIndex]);

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && itemCount > 0) {
      e.preventDefault();
      setActiveIndex((activeIndex + 1) % itemCount);
    } else if (e.key === "ArrowUp" && itemCount > 0) {
      e.preventDefault();
      setActiveIndex((activeIndex - 1 + itemCount) % itemCount);
    } else if (e.key === "Enter") {
      if (activeIndex >= 0 && activeIndex < itemCount) {
        e.preventDefault();
        handlers.onSelect(activeIndex);
        setActiveIndex(-1);
      } else if (handlers.onCommitInput) {
        e.preventDefault();
        handlers.onCommitInput();
      }
    } else if (e.key === "Escape") {
      handlers.onClose?.();
      setActiveIndex(-1);
    }
  }

  const optionId = (i: number) => `${listboxId}-opt-${i}`;

  return {
    activeIndex,
    setActiveIndex,
    listboxId,
    optionId,
    inputProps: {
      role: "combobox" as const,
      "aria-expanded": itemCount > 0,
      "aria-controls": listboxId,
      "aria-activedescendant": activeIndex >= 0 ? optionId(activeIndex) : undefined,
      "aria-autocomplete": "list" as const,
      onKeyDown,
    },
  };
}
```

### Step 3 — `.pop-in` utility in `frontend/src/globals.css`

Add after the `card-in` keyframe:

```css
/* Floating panels (suggestion lists, pickers) settle from 97% scale. */
@keyframes pop-in {
  from {
    opacity: 0;
    transform: scale(0.97);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
```

Add inside `@layer utilities`:

```css
  .pop-in {
    animation: pop-in var(--duration-press) var(--ease-out);
  }
```

Add `.pop-in` to the reduced-motion fallback group (same rule as `.animate-card-in` etc. from plan 001):

```css
  .animate-card-in,
  .slide-in-left,
  .slide-up,
  .pop-in {
    animation: fade-in var(--duration-fast) ease both;
  }
```

### Step 4 — Migrate `top-bar.tsx`

1. Delete the shared mousedown effect (lines 125-133) and the `tasksRef`/`profileRef` declarations (lines 109-110); remove `useRef` from the react import if now unused.
2. Tasks popover — replace the wrapper (line 166 `<div ref={tasksRef} className="relative hidden sm:flex">` through its closing `</div>`):

```tsx
        <DropdownMenu
          open={tasksOpen}
          onOpenChange={setTasksOpen}
          align="end"
          role="dialog"
          className="hidden sm:flex"
          trigger={
            <button
              type="button"
              data-menu-trigger
              onClick={() => setTasksOpen((v) => !v)}
              className="relative text-muted-foreground hover:text-foreground p-1 rounded-full hover:bg-muted transition-colors"
              aria-label="Notifications"
              title="Notifications"
              aria-haspopup="dialog"
              aria-expanded={tasksOpen}
            >
              {/* bell icon + activity dot exactly as before (lines 174-178) */}
            </button>
          }
        >
          {tasksOpen || true ? (
            <TaskPopover
              tasks={tasks}
              onClear={() => {
                clearCompletedTasks();
                setTasks(listTasks());
              }}
            />
          ) : null}
        </DropdownMenu>
```

(The `{tasksOpen || true ? … : null}` above is illustrative noise — render `<TaskPopover …/>` unconditionally as the child; the primitive controls mounting.) Then strip positioning from `TaskPopover`'s root (line 301): `absolute right-0 top-full mt-2 w-[360px] max-w-[calc(100vw-2rem)] rounded border border-border bg-popover shadow-lg` → `w-[360px] max-w-[calc(100vw-2rem)] rounded border border-border bg-popover shadow-lg`.

3. Profile menu — same shape: wrapper `<div ref={profileRef} className="relative hidden sm:block">` becomes `DropdownMenu` with `role="menu"`, `className="hidden sm:block"`, the existing avatar `<button>` (lines 203-219) as `trigger` (add `data-menu-trigger`; it already has `aria-haspopup`/`aria-expanded`), and `<ProfileMenu …/>` as child. Keep the `!loading && !user` login-link branch outside the primitive exactly as it is. Strip from `ProfileMenu`'s root div (line 253-256): `role="menu"` (the primitive's content div now carries it) and the positioning classes — `absolute right-0 top-full mt-3 w-48 overflow-hidden rounded border …` → `w-48 overflow-hidden rounded border …`. `ProfileMenu`'s `onNavigate`/`onLogout` callbacks must also close the menu — they already do via `onNavigate={() => setProfileOpen(false)}`.

### Step 5 — Migrate `slicer-open-button.tsx`

Delete the outside-click effect (lines 57-66) and the `ref` (line 55); replace the return block (lines 96-121):

```tsx
  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      align="end"
      role="menu"
      trigger={
        <button
          data-menu-trigger
          onClick={() => setOpen((o) => !o)}
          title="Open in slicer"
          aria-haspopup="menu"
          aria-expanded={open}
          className="inline-flex items-center gap-0.5 text-on-surface-variant hover:text-primary p-2 rounded hover:bg-surface-container-high transition-colors"
        >
          <ExternalLink className={iconSize} />
          <ChevronDown className={chevronSize} />
        </button>
      }
      contentClassName="min-w-[10rem] rounded border border-outline-variant bg-surface shadow-lg"
    >
      <p className="px-3 py-1.5 font-mono text-3xs uppercase tracking-wider text-on-surface-variant border-b border-outline-variant">
        Open in slicer
      </p>
      {slicers.map(({ name, scheme }) => (
        <button
          key={scheme}
          type="button"
          role="menuitem"
          onClick={() => openInSlicer(scheme)}
          className="block w-full px-3 py-2 text-left font-mono text-xs text-on-surface hover:bg-surface-container-low focus-visible:bg-surface-container-low outline-none transition-colors last:rounded-b"
        >
          {name}
        </button>
      ))}
    </DropdownMenu>
  );
```

(Class names shown post-002; if 002 hasn't run, keep the `[var(--…)]` forms. Note `role="menuitem"` + `focus-visible:bg-…` added to items so keyboard roving is visible.)

### Step 6 — Rewrite `ChipEditor` in `batch-toolbar.tsx` (worked example for all three chip editors)

In `ChipEditor` (line 273), after the `canCreate` computation, build a flat item list and wire the hook:

```tsx
  const items = [...filtered.map((t) => t.name), ...(canCreate ? [input.trim()] : [])];
  const nav = useComboboxNav(input ? items.length : 0, {
    onSelect: (i) => commit(items[i]),
    onCommitInput: () => {
      if (input.trim()) commit(input);
    },
  });
```

Replace the input's `onKeyDown` (lines 318-325) and add the ARIA props:

```tsx
        <input
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            nav.setActiveIndex(-1);
          }}
          {...nav.inputProps}
          onKeyDown={(e) => {
            nav.inputProps.onKeyDown(e);
            if (e.defaultPrevented) return;
            if (e.key === "Backspace" && !input && values.length) {
              onChange(values.slice(0, -1));
            }
          }}
          placeholder={…unchanged…}
          className={…unchanged…}
        />
```

Suggestion list (line 331): add `pop-in`, listbox semantics, active highlight; z-50 → `z-dropdown`:

```tsx
        {input && (filtered.length > 0 || canCreate) && (
          <div
            id={nav.listboxId}
            role="listbox"
            className="pop-in absolute left-0 right-0 top-full mt-1 z-dropdown bg-background border border-border rounded shadow-lg py-1 max-h-40 overflow-y-auto"
          >
            {filtered.map((t, i) => (
              <button
                key={t.id}
                id={nav.optionId(i)}
                role="option"
                aria-selected={i === nav.activeIndex}
                type="button"
                onClick={() => commit(t.name)}
                className={`w-full text-left px-3 py-1.5 font-mono text-xs text-muted-foreground hover:bg-muted flex justify-between gap-2 ${i === nav.activeIndex ? "bg-muted" : ""}`}
              >
                …unchanged children…
              </button>
            ))}
            {canCreate && (
              <button
                type="button"
                id={nav.optionId(filtered.length)}
                role="option"
                aria-selected={filtered.length === nav.activeIndex}
                onClick={() => commit(input)}
                className={`w-full text-left px-3 py-1.5 font-mono text-xs text-primary hover:bg-muted flex items-center gap-2 ${filtered.length === nav.activeIndex ? "bg-muted" : ""}`}
              >
                <Plus className="h-3 w-3" /> Create &quot;{input.trim()}&quot;
              </button>
            )}
          </div>
        )}
```

### Step 7 — Apply the Step 6 recipe to the other two chip editors

- `frontend/src/components/upload-modal.tsx` tag input (input around `:1150-1157`, list at `:1160`): identical transformation (items = filtered slice + optional create row; note it slices to 6 — build `items` from the same sliced array you render). List classes gain `pop-in`, `z-30` → `z-dropdown`.
- `frontend/src/components/model-detail/overview-tab.tsx` tag editor (input around `:168`, list at `:171`): same; its option rows are `<div>`s wrapping a select-button and a delete-button — put `role="option"`/`id`/`aria-selected` on the wrapping div and highlight it. `z-50` → `z-dropdown`.

If either file's structure deviates from the ChipEditor shape in a way that makes the mapping ambiguous, STOP on that file and report rather than guessing.

### Step 8 — Migrate the two category pickers to `DropdownMenu`

- `frontend/src/components/upload-modal.tsx` (`catOpen`, trigger button above line 1054): wrap trigger + list in `<DropdownMenu open={catOpen} onOpenChange={setCatOpen} align="start" role="listbox" contentClassName="left-0 right-0 …existing panel classes minus positioning…">`, delete the `fixed inset-0 z-20` click-catcher div (lines 1055-1058), strip `absolute left-0 right-0 top-full mt-1 z-30` from the list div, and give each option button `role="option"`. The panel must span the trigger width: pass `contentClassName="w-full …"` (the wrapper div is `relative` and matches the trigger's width — verify visually).
- `frontend/src/components/model-detail/overview-tab.tsx` (`editor.catOpen`, lines 93-100): same transformation; delete the `fixed inset-0 z-40` catcher (line 95).

## Boundaries

- Do NOT touch the notifications-panel, filter-sidebar, or native `<select>` elements — out of scope.
- Do NOT add floating-ui/Radix or any dependency.
- Do NOT change what any menu item does — only structure, semantics, and motion.
- Do NOT change `TaskPopover`/`ProfileMenu` content markup beyond their root positioning classes.
- If a "before" excerpt doesn't match (drift, or plan 002/003 changed the line), match on the structural anchor (state variable, `fixed inset-0` catcher, suggestion-list conditional) — if still ambiguous, STOP and report.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` && `pnpm --dir frontend lint` && `pnpm --dir frontend test` pass. `grep -rn "addEventListener(\"mousedown\"" frontend/src/components` → 0 hits. `grep -rn "z-50" frontend/src/components/top-bar.tsx frontend/src/components/slicer-open-button.tsx` → 0 floating-panel hits.
- **Feel check** (`pnpm --dir frontend dev`):
  - Open the profile menu: it scales in from its top-right corner (near the avatar), not from center; ArrowDown walks the items; Escape closes and returns focus to the avatar button; clicking elsewhere closes it.
  - In DevTools Animations panel at 10% speed, confirm the menu grows from ~95% scale, opacity ramping together.
  - In batch tag editing: type a letter, press ArrowDown twice — the highlight moves and wraps; Enter picks the highlighted tag; with no highlight, Enter still creates/commits the typed text; Backspace on empty input still removes the last chip.
  - Reduced motion: menus fade without scaling.
- **Done when**: every floating panel animates from its trigger origin, closes on Escape/outside-pointerdown, and is arrow-key navigable; no bespoke outside-click effects remain in the migrated files.

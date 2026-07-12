# 005 — Harden the control primitives and route feature styling through them

- **Status**: DONE
- **Commit**: 57770ff
- **Depends on**: 001 (motion tokens), 002 (`--primary-hover`, success/warning tokens, `text-2xs`/`text-3xs`)
- **Severity**: HIGH
- **Category**: Design system / Component architecture / Physicality (press feedback)
- **Estimated scope**: 7 primitive files (3 new), 5 feature files migrated

## Problem

The well-designed primitives are the ones nobody imports: `ui/button.tsx` and `ui/input.tsx` have **zero** consumers, while the app contains ~256 raw `<button>`s and ~102 raw `<input>`s styled by per-file string constants that all disagree:

```tsx
/* frontend/src/components/settings-panel.tsx:131-138 — current */
const BTN_PRIMARY =
  "inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] text-xs font-medium uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed";
```

```tsx
/* frontend/src/components/notifications-panel.tsx:35-38 — current: different font, no justify-center */
const BTN_PRIMARY =
  "inline-flex items-center gap-1.5 px-3 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-opacity";
```

```tsx
/* frontend/src/components/printer-detail.tsx:70-73 — current: different radius, padding, disabled opacity */
const BTN_SECONDARY =
  "inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40";
```

Further defects: `Button` has no press feedback and no loading state (62 ad-hoc `animate-spin` spinners exist); `Button`/`Badge` use `bg-primary/90`-style opacity modifiers that **silently do nothing** because the color tokens are plain hex vars; `Checkbox` isn't `forwardRef`, has no `disabled`; `Badge` has no success/warning variants (so emerald/amber are hardcoded app-wide); tabs are hand-rolled three ways with no ARIA (`printer-detail.tsx:1117`, `model-detail/index.tsx:613`, `settings-panel.tsx:762`); empty states are re-invented per view; `CardTitle` is `text-2xl` — oversized for this dense dashboard, which is why `Card` has one consumer.

## Target

- `Button`: press feedback (`active:scale-[0.98]`, transform in the transition list, 150ms), `loading` prop, `xs`/`icon-sm` sizes, working hover shades (`--primary-hover`, no broken `/90` modifiers).
- `Input`: exported `inputClasses`, `aria-invalid` styling.
- `Checkbox`: `forwardRef`, `disabled`, token colors.
- `Badge`: `success`/`warning` variants; no focus ring (it's not interactive).
- New: `Spinner`, `EmptyState`, `TabBar` (ARIA tabs + sliding active indicator).
- The per-file `BTN_*`/`INPUT` constants become thin wrappers over `buttonVariants`/`inputClasses` (call sites untouched, styling routed through the system).

## Repo conventions to follow

- Primitives: CVA + `cn()` + `forwardRef`, shadcn-style (see current `ui/button.tsx`).
- App voice: compact controls, `font-mono uppercase tracking-wider` labels in panels — expressed per call site on top of neutral primitives, not baked into them.

## Steps

### Step 1 — Rewrite `frontend/src/components/ui/button.tsx` (complete listing)

```tsx
import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { Loader2 } from "lucide-react"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-[color,background-color,border-color,box-shadow,transform,opacity] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary-hover",
        destructive:
          "bg-destructive text-destructive-foreground hover:opacity-90",
        outline:
          "border border-input bg-background hover:bg-muted",
        secondary:
          "bg-secondary text-secondary-foreground hover:opacity-90",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2",
        xs: "h-8 rounded px-3 text-xs",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
        icon: "h-10 w-10",
        "icon-sm": "h-9 w-9 rounded",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  /** Shows a spinner and disables the button. Ignored with asChild. */
  loading?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant, size, asChild = false, loading = false, disabled, children, ...props },
    ref
  ) => {
    if (asChild) {
      return (
        <Slot
          className={cn(buttonVariants({ variant, size, className }))}
          ref={ref}
          {...props}
        >
          {children}
        </Slot>
      )
    }
    return (
      <button
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        disabled={disabled || loading}
        {...props}
      >
        {loading && (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        )}
        {children}
      </button>
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
```

(Changes vs current: `gap-1.5` in base; transition list includes `transform`; `active:scale-[0.98]` press feedback per the 0.95–0.98 rule; `hover:bg-primary/90` → `hover:bg-primary-hover` and other broken `/`-modifiers → `hover:opacity-90` or solid hovers; `outline` hover uses `bg-muted` to match the app's dominant secondary-button hover; new `xs` and `icon-sm` sizes; `loading` prop.)

### Step 2 — Update `frontend/src/components/ui/input.tsx`

Extract and export the class string, and add invalid-state styling. Complete listing:

```tsx
import * as React from "react"

import { cn } from "@/lib/utils"

export const inputClasses =
  "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-destructive aria-[invalid=true]:focus-visible:ring-destructive"

export interface InputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(inputClasses, className)}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }
```

### Step 3 — Rewrite `frontend/src/components/ui/checkbox.tsx` (complete listing)

```tsx
"use client";

import { forwardRef } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export const Checkbox = forwardRef<
  HTMLButtonElement,
  {
    checked: boolean;
    onChange: (checked: boolean) => void;
    className?: string;
    ariaLabel?: string;
    disabled?: boolean;
  }
>(({ checked, onChange, className, ariaLabel, disabled }, ref) => {
  return (
    <button
      ref={ref}
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onChange(!checked);
      }}
      className={cn(
        "flex h-5 w-5 items-center justify-center rounded border transition-[color,background-color,border-color,transform] duration-press active:scale-[0.95] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-50",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-background/80 text-transparent hover:border-primary",
        className,
      )}
    >
      <Check className="h-3.5 w-3.5" strokeWidth={3} />
    </button>
  );
});
Checkbox.displayName = "Checkbox";
```

(If plan 002 already tokenized the colors, keep that; this listing is the final state either way. The two existing consumers pass `checked`/`onChange`/`ariaLabel` only, so the API is backward-compatible.)

### Step 4 — Update `frontend/src/components/ui/badge.tsx`

Replace the `badgeVariants` definition with (rest of file unchanged):

```tsx
const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground",
        success: "border-transparent bg-success text-success-foreground",
        warning: "border-transparent bg-warning text-warning-foreground",
        outline: "text-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)
```

(Drops the focus ring — badges aren't focusable — and the broken `hover:bg-*/80` modifiers.)

### Step 5 — New `frontend/src/components/ui/spinner.tsx` (complete listing)

```tsx
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const SIZES = {
  sm: "h-3.5 w-3.5",
  md: "h-5 w-5",
  lg: "h-6 w-6",
} as const;

export function Spinner({
  size = "md",
  className,
  label = "Loading",
}: {
  size?: keyof typeof SIZES;
  className?: string;
  label?: string;
}) {
  return (
    <Loader2
      role="status"
      aria-label={label}
      className={cn("animate-spin", SIZES[size], className)}
    />
  );
}
```

### Step 6 — New `frontend/src/components/ui/empty-state.tsx` (complete listing)

```tsx
import { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-16 px-6 text-center text-muted-foreground",
        className,
      )}
    >
      {Icon && <Icon className="h-12 w-12 opacity-30" aria-hidden />}
      <p className="mt-3 text-lg font-medium text-foreground">{title}</p>
      {description && <p className="mt-1 text-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
```

### Step 7 — New `frontend/src/components/ui/tabs.tsx` (complete listing)

```tsx
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
}: {
  tabs: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  className?: string;
  tabClassName?: string;
  activeTabClassName?: string;
  indicatorInset?: number;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      const el = list.querySelector<HTMLElement>('[data-active="true"]');
      if (el) setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
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
      {indicator && (
        <span
          aria-hidden
          className="absolute bottom-0 h-0.5 rounded-full bg-primary transition-[transform,width] duration-fast ease-in-out motion-reduce:transition-none"
          style={{
            width: Math.max(0, indicator.width - indicatorInset * 2),
            transform: `translateX(${indicator.left + indicatorInset}px)`,
          }}
        />
      )}
    </div>
  );
}
```

### Step 8 — `CardTitle` density fix

`frontend/src/components/ui/card.tsx:39`: `"text-2xl font-semibold leading-none tracking-tight"` → `"text-base font-semibold leading-none tracking-tight"`. Check the single existing `Card` consumer (`grep -rln "ui/card" frontend/src --include="*.tsx"`) still looks right; if it relied on the 2xl size, add `className="text-2xl"` at that call site.

### Step 9 — Bridge the per-file style constants

**`frontend/src/components/settings-panel.tsx:131-138`** — replace the four constants with:

```tsx
import { buttonVariants } from "@/components/ui/button";
import { inputClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const BTN_PRIMARY = cn(buttonVariants({ size: "xs" }), "uppercase tracking-wider");
const BTN_SECONDARY = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "uppercase tracking-wider text-muted-foreground",
);
const BTN_ICON = buttonVariants({ variant: "outline", size: "icon-sm" });
const INPUT = cn(inputClasses, "h-auto py-2 rounded");
```

**`frontend/src/components/notifications-panel.tsx:33-39`**:

```tsx
const INPUT = cn(inputClasses, "h-auto px-2.5 py-1.5 rounded placeholder:text-muted-foreground/40");
const BTN_PRIMARY = cn(buttonVariants({ size: "xs" }), "font-mono uppercase tracking-wider");
const BTN_SECONDARY = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "font-mono uppercase tracking-wider text-muted-foreground",
);
const LABEL = "block text-2xs text-muted-foreground mb-1";
```

**`frontend/src/components/printer-detail.tsx:70-73`**:

```tsx
const BTN_SECONDARY = cn(buttonVariants({ variant: "outline", size: "xs" }), "hover:bg-muted");
const BTN_DANGER = cn(
  buttonVariants({ variant: "outline", size: "xs" }),
  "border-red-300/50 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40",
);
```

Add the needed imports in each file. Call sites keep using the constants — zero call-site churn, but every button now inherits press feedback, focus-visible rings, and token hovers. Minor visual deltas (padding/radius normalization) are intended.

### Step 10 — Adopt `Button` in `frontend/src/components/ui/confirm-modal.tsx`

Replace the two raw buttons (lines 36-53) with:

```tsx
      <div className="flex gap-3 mt-6">
        <Button
          type="button"
          variant="outline"
          onClick={onClose}
          disabled={busy}
          className="flex-1 h-9 font-mono uppercase tracking-wider text-muted-foreground"
        >
          Cancel
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={onConfirm}
          loading={busy}
          className="flex-1 h-9 font-mono uppercase tracking-wider"
        >
          {confirmLabel}
        </Button>
      </div>
```

Remove the now-unused `Loader2` import. (Dark-mode delta: destructive becomes token red `#f87171` with dark text instead of `bg-red-600`/white — intended.)

### Step 11 — Migrate the three tab strips to `TabBar`

**`frontend/src/components/printer-detail.tsx`**: delete the `TabButton` component (lines 1117-1139) and replace the usage block (lines 462-482) with:

```tsx
<TabBar
  tabs={[
    { key: "status", label: "Status" },
    { key: "files", label: "Files" },
    { key: "jobs", label: "Jobs" },
    ...(/* preserve the existing condition guarding the config tab */ [] as const),
    { key: "diagnostics", label: "Diagnostics" },
  ]}
  active={activeTab}
  onChange={(k) => {
    setActiveTab(k);
    /* fold in any side effects from the old config-tab onClick (lines 473-478) */
  }}
  className={/* the old strip's container classes (border-b etc.) */ ""}
  tabClassName="px-3 py-2 font-mono text-xs uppercase tracking-wider transition-colors text-muted-foreground hover:text-foreground"
  activeTabClassName="text-primary"
/>
```

The old `TabButton` used a `border-b-2` underline; the TabBar indicator replaces it — remove per-button borders. **Read lines 460-485 first**: the config tab is conditionally shown and its `onClick` runs extra logic — build the `tabs` array conditionally and move that logic into `onChange`. If the logic is unclear, STOP and report.

**`frontend/src/components/model-detail/index.tsx`** (lines 612-633): replace the `<nav>` block with `TabBar`, `tabs={visibleTabs.map((tab) => ({ key: tab.key, label: <>{tab.label}{tab.key === "revisions" && gcodeFiles.length > 0 && <span className="ml-1 opacity-60">{gcodeFiles.length}</span>}</> }))}`, `active={activeTab}`, `onChange={setActiveTab}`, `indicatorInset={8}`, `className` = the old nav's classes (`flex shrink-0 border-b border-outline-variant bg-surface-container-lowest px-2 overflow-x-auto` + the scrollbar-hiding arbitrary classes), `tabClassName="px-3 py-3 font-mono text-2xs uppercase tracking-wider whitespace-nowrap transition-colors text-on-surface-variant hover:text-on-surface"`, `activeTabClassName="text-primary"`. Delete the per-button absolute indicator `<span>` (lines 628-630).

**`frontend/src/components/settings-panel.tsx`** (lines 758-780): same shape — `tabs={SETTINGS_SECTIONS.map((s) => ({ key: s.id, label: <><s.icon className="h-4 w-4" />{s.label}</> }))}`, `className="flex gap-1 overflow-x-auto -mb-px"` on TabBar (parent `border-b border-border` wrapper stays), `tabClassName="relative inline-flex items-center gap-2 whitespace-nowrap px-3.5 py-2.5 text-sm font-medium transition-colors text-muted-foreground hover:text-foreground"`, `activeTabClassName="text-primary"`. Remove the old `border-b-2` classes.

### Step 12 — Adopt `EmptyState` at the two worst sites

**`frontend/src/components/model-grid.tsx:727-742`**: replace the empty-state div with `<EmptyState title="No models found" description={…same conditional string…} action={…the existing wiki <a>, only when the no-filters condition holds…} className="flex-1 py-20" />`.

**`frontend/src/components/printers-list.tsx:104-114`**: replace with `<EmptyState icon={PrinterIcon} title="No printers configured yet." action={<Button size="xs" onClick={() => setAddOpen(true)}><Plus className="h-3.5 w-3.5" />Add your first printer</Button>} className="bg-card border border-border rounded" />`.

## Boundaries

- Do NOT sweep the 62 ad-hoc spinners or the 100+ raw inputs/buttons beyond the files named here — deferred; the constants bridge captures the biggest files.
- Do NOT change any handler logic, disabled conditions, or copy.
- Do NOT add tabpanel `id`/`aria-controls` plumbing (accepted gap for now).
- Do NOT add new dependencies.
- If any "before" excerpt has drifted, STOP and report.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` && `pnpm --dir frontend lint` && `pnpm --dir frontend test` && `pnpm --dir frontend build` pass. `grep -rn "hover:bg-primary/90\|ring-ring/\|bg-destructive/80" frontend/src/components/ui` → 0.
- **Feel check**:
  - Press-and-hold any settings button: it compresses to 98% smoothly (150ms), springs back on release; keyboard focus shows a ring, mouse click does not.
  - Switch tabs on a model detail page: the underline **slides** between tabs (and retargets if you click quickly), instead of blinking out/in; ArrowLeft/Right move between tabs when the strip has focus.
  - Confirm-delete dialog: the destructive button shows an inline spinner while busy.
- **Done when**: all listed files compile against the new primitives, the three tab strips are ARIA tablists with a sliding indicator, and the `BTN_*` constants delegate to `buttonVariants`.

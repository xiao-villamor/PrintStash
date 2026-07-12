# 007 — Soften the hard state swaps (tabs, view toggle, empty states)

- **Status**: DONE
- **Commit**: 57770ff
- **Depends on**: 001 (motion tokens). Best run AFTER 005 (the tab strips become `TabBar`; this plan animates the *content* they switch).
- **Severity**: MEDIUM
- **Category**: Missed opportunities
- **Estimated scope**: 1 CSS utility + 4 components

## Problem

Several frequent state changes teleport where a barely-there entrance would prevent a jarring cut:

1. **Model-detail tab content** hard mounts/unmounts on tab switch (`frontend/src/components/model-detail/index.tsx:636-694` — `{activeTab === "overview" && (<OverviewTab …/>)}` etc.).
2. **Settings sections** swap the whole panel instantly (`frontend/src/components/settings-panel.tsx:782+` — `{activeSection === "overview" && (<div className="space-y-6">…`).
3. **Printer-detail tab panels** likewise (`frontend/src/components/printer-detail.tsx` — `{activeTab === "status" && …}` blocks after line 483).
4. **Grid ↔ list view toggle** replaces the entire listing layout in one frame (`frontend/src/components/model-grid.tsx:744` grid container vs `:767` list container).
5. **The "No models found" empty state** hard-cuts in when a filter clears the grid (`model-grid.tsx:725-742`; after plan 005 this is an `<EmptyState …/>`).

## Target

One shared utility — opacity 0→1 + 4px rise, 150ms strong ease-out — applied to the mounting panel in each case. Subtle enough for surfaces switched many times per session (frequency rule: reduce, don't showcase), but enough to read as a settle instead of a cut.

## Repo conventions to follow

- Keyframes at top level of `frontend/src/globals.css`, appliers in `@layer utilities`, reduced-motion fallback in the media block at the bottom (all per plan 001's layout).
- `card-in` (4px rise) is the visual sibling — `panel-in` uses the same distance at a shorter duration.

## Steps

### Step 1 — `panel-in` utility in `frontend/src/globals.css`

Add after the `card-in` keyframe:

```css
/* Content panels (tab bodies, section swaps) settle in with a 4px rise. */
@keyframes panel-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
```

Add inside `@layer utilities`:

```css
  .animate-panel-in {
    animation: panel-in var(--duration-press) var(--ease-out) both;
  }
```

Extend the reduced-motion fallback group (plan 001's rule; plan 004 added `.pop-in`):

```css
  .animate-card-in,
  .slide-in-left,
  .slide-up,
  .pop-in,
  .animate-panel-in {
    animation: fade-in var(--duration-fast) ease both;
  }
```

### Step 2 — Model detail tab content

`frontend/src/components/model-detail/index.tsx:635` — the scrollable content container:

```tsx
/* before */
          <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 md:space-y-8 [scrollbar-width:thin] …">

/* after — keyed by tab so it remounts (and re-runs the entrance) per switch */
          <div key={activeTab} className="animate-panel-in flex-1 overflow-y-auto p-4 md:p-6 space-y-6 md:space-y-8 [scrollbar-width:thin] …">
```

Keep every other class exactly as found. Side effect (intended): the scroll position resets to top when switching tabs.

### Step 3 — Settings sections

In `frontend/src/components/settings-panel.tsx`, run `grep -n 'activeSection === ' frontend/src/components/settings-panel.tsx`. For **each** conditional block of the form `{activeSection === "x" && (<div className="…">` append ` animate-panel-in` to that root div's className (example, line 782-783: `<div className="space-y-6">` → `<div className="space-y-6 animate-panel-in">`). If a block's root is a component or fragment rather than an element, wrap it in `<div className="animate-panel-in">…</div>` instead. The root only mounts when its section activates, so no `key` is needed.

### Step 4 — Printer detail tab panels

Same recipe in `frontend/src/components/printer-detail.tsx` for each `{activeTab === "…" && …}` panel after the tab strip (~line 483 onward): append ` animate-panel-in` to the panel's root element className, or wrap fragments/components in `<div className="animate-panel-in">`. Do not touch the poll/refresh logic inside the panels.

### Step 5 — Grid ↔ list toggle in `frontend/src/components/model-grid.tsx`

React reuses an unkeyed `<div>` across the two branches (same element type, same position), so no remount and no animation would fire — the `key`s below force it:

```tsx
/* before (line 744) */          ) : viewMode === "grid" ? (
            <div className="p-4 sm:p-6">
/* after */                      ) : viewMode === "grid" ? (
            <div key="grid" className="p-4 sm:p-6 animate-panel-in">

/* before (line 767) */          ) : (
            <div className="flex-1 overflow-y-auto">
/* after */                      ) : (
            <div key="list" className="flex-1 overflow-y-auto animate-panel-in">
```

(Exact classes may differ slightly at execution time — the anchor is the `viewMode === "grid" ?` ternary; add `key` + `animate-panel-in` to both branch containers.)

### Step 6 — Empty state fade

`frontend/src/components/model-grid.tsx:725-742` (post-005: the `<EmptyState …/>` for "No models found"): add `animate-panel-in` to its `className` prop.

## Boundaries

- Do NOT animate the tab *strips* (that's the TabBar indicator from plan 005) — only content panels.
- Do NOT add exit animations, crossfades, or staggering — entrance-only, 150ms, one utility.
- Do NOT apply `animate-panel-in` anywhere not listed (it must not creep onto high-frequency re-renders like search results — cards already have `card-in`).
- Do NOT add new dependencies.
- If an anchor can't be found (drift), STOP on that step and report.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` && `pnpm --dir frontend lint` && `pnpm --dir frontend test` pass.
- **Feel check**:
  - Switch tabs on a model detail page rapidly: each panel settles in with a barely-visible rise; nothing flashes or double-animates; the TabBar indicator (from 005) slides in sync.
  - Toggle grid ↔ list in the vault: the new layout fades/rises in instead of hard-cutting.
  - Search for gibberish so the grid empties: "No models found" settles in rather than popping.
  - DevTools reduced-motion: panels fade only (no rise).
- **Done when**: all five swap sites animate their entrance and nothing else in the app gained motion.

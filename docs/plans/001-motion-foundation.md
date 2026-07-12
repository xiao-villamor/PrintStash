# 001 — Establish motion tokens and a global reduced-motion policy

- **Status**: DONE
- **Commit**: 57770ff
- **Severity**: HIGH
- **Category**: Cohesion & tokens / Accessibility
- **Estimated scope**: 3 files (`frontend/src/globals.css`, `frontend/tailwind.config.cjs`, `frontend/src/components/theme-toggle.tsx`), small diff

## Problem

The app has no motion tokens. Five distinct easing curves and eight distinct durations are hand-typed at call sites, one curve is typed twice, one curve is a bouncy back-out that clashes with the app's crisp dashboard personality, and only one of five movement animations is gated behind `prefers-reduced-motion`.

```css
/* frontend/src/globals.css:236-260 — current */
@layer utilities {
  .animate-theme-icon {
    animation: theme-icon-in 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
  }

  .slide-in-left {
    animation: slide-in-left 0.2s ease-out;
  }

  .slide-up {
    animation: slide-up 0.22s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .fade-in {
    animation: fade-in 0.18s ease-out;
  }

  .animate-card-in {
    animation: card-in 0.3s cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  .pb-safe {
    padding-bottom: env(safe-area-inset-bottom, 8px);
  }
}

/* Respect reduced-motion: keep the cards, drop the movement. */
@media (prefers-reduced-motion: reduce) {
  .animate-card-in {
    animation: none;
  }
}
```

Specific defects:

1. `cubic-bezier(0.16, 1, 0.3, 1)` is typed twice (`globals.css:246` and `:254`) with no shared token.
2. `.animate-theme-icon` uses `cubic-bezier(0.34, 1.56, 0.64, 1)` — the `1.56` control point overshoots (bouncy back-out). Every other motion in the app is crisp. One bouncy component in a crisp app is a cohesion defect. It also starts from `scale(0.6)`, well below the 0.9–0.97 physicality floor:

```css
/* frontend/src/globals.css:167-170 — current */
@keyframes theme-icon-in {
  from { opacity: 0; transform: rotate(-45deg) scale(0.6); }
  to   { opacity: 1; transform: rotate(0deg) scale(1); }
}
```

3. `.slide-in-left` (mobile drawers) and `.slide-up` (bottom sheet) are full-position movements with **no** reduced-motion handling; the reduced-motion block covers only `.animate-card-in`.
4. `card-in` runs at 0.3s / 8px rise on model-grid cards — a surface that re-renders on every debounced search refinement and filter change (`frontend/src/components/model-card.tsx:144`, `frontend/src/components/model-grid.tsx:854`). Frequency rule: list-navigation-frequency animation must be drastically reduced.
5. Entrance durations are scattered (180 / 200 / 220 / 250 / 300 ms) with no scale.

## Target

One tokenized motion system in `globals.css` `:root`, consumed by every keyframe utility, mirrored into Tailwind so `ease-out` / `ease-in-out` utility classes resolve to the strong curves:

```css
/* target — add inside the existing :root block in globals.css (after --radius) */
    /* Motion tokens — the single source of truth for easing and duration.
       --ease-out is the curve this codebase already used in slide-up/card-in. */
    --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
    --duration-press: 150ms;
    --duration-fast: 200ms;
    --duration-slow: 300ms;
```

```css
/* target — replacement for the utilities + keyframes listed above */
@keyframes theme-icon-in {
  from { opacity: 0; transform: rotate(-30deg) scale(0.9); }
  to   { opacity: 1; transform: rotate(0deg) scale(1); }
}

/* card-in: reduced from 0.3s/8px — this element re-enters on every search
   refinement, so the entrance must be barely-there. */
@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@layer utilities {
  .animate-theme-icon {
    animation: theme-icon-in var(--duration-fast) var(--ease-out);
  }

  .slide-in-left {
    animation: slide-in-left var(--duration-fast) var(--ease-out);
  }

  .slide-up {
    animation: slide-up var(--duration-fast) var(--ease-out);
  }

  .fade-in {
    animation: fade-in var(--duration-fast) var(--ease-out);
  }

  .animate-card-in {
    animation: card-in var(--duration-fast) var(--ease-out) both;
  }

  .pb-safe {
    padding-bottom: env(safe-area-inset-bottom, 8px);
  }
}

/* Reduced motion: keep opacity feedback, drop all movement. */
@media (prefers-reduced-motion: reduce) {
  .animate-card-in,
  .slide-in-left,
  .slide-up {
    animation: fade-in var(--duration-fast) ease both;
  }
  .animate-theme-icon {
    animation: none;
  }
}
```

```js
// target — add inside theme.extend in frontend/tailwind.config.cjs
      transitionTimingFunction: {
        out: "var(--ease-out)",
        "in-out": "var(--ease-in-out)",
      },
      transitionDuration: {
        press: "var(--duration-press)",
        fast: "var(--duration-fast)",
        slow: "var(--duration-slow)",
      },
```

(Extending the `out` / `in-out` keys makes the existing `ease-out` and `ease-in-out` Tailwind classes resolve to the strong curves — e.g. the thumbnail fade at `frontend/src/components/model-card.tsx:181` upgrades automatically. Do NOT override the `DEFAULT` timing function; the 146 `transition-colors` hover states are correct on Tailwind's default curve.)

## Repo conventions to follow

- All design tokens live as CSS custom properties in the `:root` block of `frontend/src/globals.css` (see `--radius` at `globals.css:32`) and are mapped in `frontend/tailwind.config.cjs` under `theme.extend` as `var(...)` references (see the `colors` block at `tailwind.config.cjs:19`).
- Keyframes live at top level of `globals.css`; the classes that apply them live in `@layer utilities`.
- Comments in `globals.css` are short prose explaining intent (see `globals.css:199-201`).

## Steps

1. In `frontend/src/globals.css`, add the five motion tokens shown in Target to the `:root` block, immediately after `--radius: 0.375rem;` (line 32). Do NOT add them to the `.dark` block — motion is theme-independent.
2. Replace the `theme-icon-in` keyframe (lines 167-170) with the Target version (`rotate(-30deg) scale(0.9)` start).
3. Replace the `card-in` keyframe's `translateY(8px)` (line 207) with `translateY(4px)`.
4. Replace the five animation utilities (lines 237-255) with the Target versions using `var(--duration-fast)` and `var(--ease-out)`. Keep `.pb-safe` untouched.
5. Replace the `prefers-reduced-motion` block (lines 263-267) with the Target version covering `.animate-card-in`, `.slide-in-left`, `.slide-up` (fall back to `fade-in`) and `.animate-theme-icon` (none).
6. In `frontend/tailwind.config.cjs`, add the `transitionTimingFunction` and `transitionDuration` blocks shown in Target inside `theme.extend`, as siblings of the existing `keyframes` block.
7. In `frontend/tailwind.config.cjs`, change the accordion animations (lines 100-101) from `"accordion-down 0.2s ease-out"` to `"accordion-down 0.2s var(--ease-out)"` (same for `accordion-up`) so the only remaining built-in easing reference is gone. These utilities are currently unused, so this is zero-risk.

## Boundaries

- Do NOT touch any `.tsx` file in this plan (theme-toggle behavior is unchanged; only the CSS it references changes).
- Do NOT change the `.theme-transitioning` block (`globals.css:155-165`) — bare `ease` on color-only theme swaps is correct and deliberate.
- Do NOT override Tailwind's `DEFAULT` transition timing function or duration.
- Do NOT add new dependencies.
- If a line you're told to replace doesn't match the excerpt here (drift since commit 57770ff), STOP and report instead of improvising.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` passes; `pnpm --dir frontend lint` passes; `pnpm --dir frontend build` succeeds. `grep -n "cubic-bezier" frontend/src/globals.css` returns exactly two hits, both inside the `:root` token block. `grep -rn "0.34, 1.56" frontend/src` returns nothing.
- **Feel check**: run `pnpm --dir frontend dev`.
  - Toggle the theme: the sun/moon icon should settle crisply with no overshoot/bounce.
  - Type in the model search box: cards should re-enter almost imperceptibly (4px, 200ms) — the grid must not visibly "jump" on each debounce tick.
  - In DevTools → Rendering → emulate `prefers-reduced-motion: reduce`, open the mobile nav drawer (narrow viewport): it should fade in without sliding.
- **Done when**: all mechanical checks pass and no hand-typed cubic-bezier or bare keyframe duration remains outside the `:root` token block in `globals.css`.

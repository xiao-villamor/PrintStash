# 001 — Theme swap: stop the whole-document transition storm

Commit: `a5933ac` · Severity: HIGH · File: `frontend/src/globals.css`

## Problem

The theme toggle adds `.theme-transitioning` to `<html>`, which applies a
`transition … !important` to **every element plus every `::before`/`::after`**.
It animates `box-shadow` document-wide (an expensive, non-composited property),
and its durations (`280ms`, `200ms`) and easing (`ease`) are hand-typed — both
banned by `DESIGN.md` ("Never type a raw duration or cubic-bezier into a
component", "the only curves/durations that exist").

## Current code (`globals.css`, lines 181–191)

```css
/* Smooth theme transitions — only active during the class swap, not on hovers */
.theme-transitioning,
.theme-transitioning *,
.theme-transitioning *::before,
.theme-transitioning *::after {
  transition:
    background-color 280ms ease,
    border-color 280ms ease,
    color 200ms ease,
    box-shadow 200ms ease !important;
}
```

## Target code

```css
/* Smooth theme transitions — only active during the class swap, not on hovers.
   Color properties only: box-shadow is not composited and this selector hits
   every node in the document. */
.theme-transitioning,
.theme-transitioning *,
.theme-transitioning *::before,
.theme-transitioning *::after {
  transition:
    background-color var(--duration-slow) var(--ease-out),
    border-color var(--duration-slow) var(--ease-out),
    color var(--duration-fast) var(--ease-out) !important;
}
```

`--duration-slow` = 300ms, `--duration-fast` = 200ms, `--ease-out` =
`cubic-bezier(0.16, 1, 0.3, 1)` — all already defined at the top of this file.

## Scope boundaries

- Do not touch `theme-toggle.tsx` or the class-swap timing logic.
- Do not add a reduced-motion override: a color crossfade is movement-free and
  is exactly what `prefers-reduced-motion` is supposed to keep.

## Verify

- `pnpm lint && pnpm typecheck`
- Feel-check: toggle the theme with DevTools Performance recording at 4× CPU
  throttle. The swap should be one smooth color crossfade with no layout/paint
  spike from shadows.

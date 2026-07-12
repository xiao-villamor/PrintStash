# 004 — Tab indicator: animate transform, not width

Commit: `a5933ac` · Severity: LOW · File: `frontend/src/components/ui/tabs.tsx`

## Problem

The sliding indicator animates `width`, a layout property, which `DESIGN.md`
rule 1 bans ("Animate `transform` and `opacity` only"). Cheap here (one element),
but it is the last violation of that rule in `src/` and the rule's value is that
the count stays at zero.

## Current code (lines 85–94)

```tsx
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
```

## Target code

Give the bar a fixed 1px base width and scale it. `transform-origin: left` is
required — without it the bar grows from its centre and slides wrong.

```tsx
      {indicator && (
        <span
          aria-hidden
          className="absolute bottom-0 left-0 h-0.5 w-px origin-left rounded-full bg-primary transition-transform duration-fast ease-in-out motion-reduce:transition-none"
          style={{
            transform: `translateX(${indicator.left + indicatorInset}px) scaleX(${Math.max(0, indicator.width - indicatorInset * 2)})`,
          }}
        />
      )}
```

Note `left-0` is now required: previously the element was positioned only by its
`translateX`, and it still is — but make the base offset explicit so the scale
math is anchored. Keep `ease-in-out`; this is on-screen movement, which is the
one place `DESIGN.md` allows that curve.

## Scope boundaries

Do not touch the `useState`/measurement effect that computes `indicator`, the
ARIA `tablist` wiring, or the arrow-key roving.

## Verify

- `pnpm lint && pnpm typecheck && pnpm test`
- `pnpm test:e2e` if it runs locally — `frontend/tests/e2e/app-routes.spec.ts`
  covers reduced-motion behaviour.
- Feel-check: click through tabs on the model detail page. The bar must land
  flush with each tab's label, same width as before, with no visible rounding
  distortion on the pill ends. If `scaleX` visibly warps the `rounded-full` caps
  at 0.5px height, revert to the original and report — the rule is not worth a
  visual regression.

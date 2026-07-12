# 003 — Toasts: put Sonner on the house curve

Commit: `a5933ac` · Severity: MEDIUM · Files: `frontend/src/globals.css`, `frontend/src/components/toaster.tsx`

## Problem

`Toaster` renders Sonner with default motion: Sonner's own easing and a ~400ms
enter. It is the only motion in the app that is not on the `--ease-out` /
`--duration-*` scale, and it exceeds `DESIGN.md`'s hard 300ms ceiling.

## Current code (`toaster.tsx`)

```tsx
    <SonnerToaster
      position="bottom-right"
      className="!bottom-20 md:!bottom-4 group toast"
      toastOptions={{
        style: { … }
      }}
    />
```

## Change

Sonner drives its enter/exit from CSS custom properties on `[data-sonner-toast]`.
Add to `globals.css`, immediately **after** the `@keyframes pop-in` block (keep
motion rules together):

```css
/* Sonner's defaults are slower than the house scale; pull them onto it.
   Transitions (not keyframes) so a toast dismissed mid-enter reverses cleanly. */
[data-sonner-toast] {
  transition:
    transform var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out) !important;
}
```

Do **not** hand-write `@keyframes` for toasts — Sonner already stacks/offsets
them with transforms, and keyframes would fight that.

## Scope boundaries

- Do not change `position`, the mobile `!bottom-20` offset, or the token colors
  in `toastOptions.style`.
- Do not add a new dependency or replace Sonner.
- No reduced-motion block needed: the `@media (prefers-reduced-motion: reduce)`
  section governs the app's own keyframe utilities, and Sonner honours the media
  query itself.

## Verify

- `pnpm lint && pnpm typecheck`
- Feel-check: fire two toasts back to back (any upload). They should slide in on
  the house curve and the stack should reflow smoothly, not restart.
- If the CSS override visibly does nothing (Sonner version may not expose the
  transition this way), **stop and report that** rather than escalating to
  keyframes or `!important` spirals.

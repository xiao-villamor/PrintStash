# Frontend design-system & motion plans

Written at commit `57770ff` from a six-agent audit (motion ×4, design system, component architecture) of `frontend/`. Each plan is self-contained: an executor with zero context can implement it mechanically. Every plan ends with mechanical checks (`typecheck`, `lint`, `test`, and where noted `build`) plus a feel check.

## Plans

| # | Plan | Severity | Theme | Status |
| --- | --- | --- | --- | --- |
| 001 | [Motion tokens + reduced-motion policy](001-motion-foundation.md) | HIGH | Motion foundation | DONE |
| 002 | [Unify color/token system, retire hand-encoded theme colors](002-design-tokens.md) | HIGH | Design-token foundation | PARTIAL (see plan header) |
| 003 | [Animated, focus-trapped Modal + Drawer primitives](003-overlay-system.md) | HIGH | Overlays | DONE |
| 004 | [Dropdown/Popover primitive + accessible comboboxes](004-menu-primitives.md) | HIGH | Menus | DONE |
| 005 | [Harden control primitives, route feature styling through them](005-control-primitives.md) | HIGH | Controls & tabs | DONE |
| 006 | [Kill `transition-all`, GPU progress bars, press feedback](006-interaction-feel-sweep.md) | HIGH | Interaction feel | DONE |
| 007 | [Soften hard state swaps (tab panels, view toggle, empty states)](007-state-transition-polish.md) | MEDIUM | Polish | DONE |

## Execution order & batching

Sequential waves — later plans build on earlier tokens/primitives, and several plans edit the same files (`upload-modal.tsx`, `top-bar.tsx`, `settings-panel.tsx`, `printer-detail.tsx`, `model-grid.tsx`), so parallel execution would conflict.

```
Wave 1 (one executor):  001 + 002   — foundations: motion tokens, color/token unification
Wave 2:                 003          — overlay system (needs z-overlay, motion utilities)
Wave 3:                 004          — menus/comboboxes (needs useMountTransition from 003)
Wave 4:                 005          — control primitives + tab strips (needs 001/002 tokens)
Wave 5 (one executor):  006 + 007   — sweeps & polish (line numbers drift least when run last)
```

After each wave: `pnpm --dir frontend typecheck && pnpm --dir frontend lint && pnpm --dir frontend test`; full `build` at waves 1 and 5. Update the plan's **Status** to DONE and this table when a wave lands.

## Vetted-but-deferred (not planned yet)

- **Spinner/raw-input/raw-button adoption sweep** beyond the files named in 005 (62 ad-hoc `animate-spin`, ~100 raw inputs) — mechanical follow-up once 005's primitives exist.
- **Status-color sweep** (hardcoded emerald/amber/red → `success`/`warning`/`destructive` tokens added in 002) — do per-view.
- **Upload-success delight moment** (in-modal completion state) — needs a design pass on `upload-modal.tsx`'s completion flow first.
- **`cn()` adoption in feature files** (template-literal className concatenation) — fold into future touches.
- **Remove dead `@radix-ui/react-icons` dependency** (zero imports) and hand-rolled `ui/separator.tsx` vs the installed Radix separator — housekeeping PR.
- **Sonner reduced-motion**: sonner ≥2 handles `prefers-reduced-motion` internally; verify once during QA.
- **Tab panel `aria-controls`/`id` plumbing** — accepted gap in 005's TabBar.

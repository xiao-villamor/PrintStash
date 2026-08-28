# Frontend

Vite + React + TS in `frontend/src/{pages,components,lib,types}`.
**`DESIGN.md` is binding** — read it before adding or restyling anything; this
file only routes and adds the checklist.

## UI change checklist

- [ ] Compose existing primitives from `src/components/ui/` (button, card,
      modal, drawer, dropdown-menu, tabs, confirm-modal, empty-state, …).
      Never hand-roll an overlay.
- [ ] Semantic tokens only: `bg-primary` + `text-primary-foreground` pairs,
      `ring-ring`, named radius/type/z scales. No `[var(--…)]`, no
      `text-white` on primary, no `rounded-[6px]`, no `z-[60]`.
- [ ] Motion: only `ease-out`/`ease-in-out` + `duration-press|fast|slow`.
      The zero-counts are load-bearing: no `transition-all`, no `ease-in`, no
      raw durations/cubic-beziers, nothing over 300ms, route navigation never
      animates. `duration-200` is a finding — it's `duration-fast`.
- [ ] New value needed? Add it as a token in `globals.css` +
      `tailwind.config.cjs`, never as a one-off.
- [ ] Dark theme: primary swaps blue→orange automatically via tokens — never
      hand-encode the swap.
- [ ] Provider/feature gating: disable unsupported actions from the API's
      capability flags; label beta providers as beta. Don't present roadmap
      features as available.

## Validate

```bash
cd frontend
pnpm lint          # oxlint (+ vendored anti-slop plugin) — report the exact count from the run
pnpm format:check  # oxfmt — formatting is a CI gate; `pnpm format` writes
pnpm typecheck     # tsc --noEmit, root + both workspace packages
pnpm test          # vitest, when logic changed
```

Writing or changing tests (vitest or Playwright): `.agents/skills/create-tests/SKILL.md`
and its `references/frontend.md` / `references/playwright.md`.

The frontend lints with **oxlint**, formats with **oxfmt**, and typechecks with the
**TypeScript 7 native compiler**. There is no ESLint and no prettier — do not
reintroduce them, and do not hand-format: run `pnpm format`.

`tools/oxlint/anti-slop/` is a vendored copy of the
[anti-slop](https://github.com/dmmulroy/anti-slop) plugin. Its rules reject
low-evidence typing: `unknown` in a contract, `as` chains, `Record<string,
unknown>` dictionaries, `typeof` narrowing instead of boundary parsing, and
`vi.mock` module mocks. When one fires, **fix the typing** — never add a cast, a
blanket `SAFETY:` comment, or a rule suppression to get past it. A `SAFETY:`
comment must state the specific checked invariant for that one site.

To see edits live: `:3000` is the prebuilt Docker image (no HMR) — run
`pnpm dev` on a spare port instead.

## Design history

The motion/token system was built through `docs/plans/001–007` (motion
foundation → state-transition polish). Consult those only when questioning why
a rule exists; `DESIGN.md` is the current binding summary.

# 006 — One page header, and Printers' buttons through the primitive

Commit: `ef5f703` · Severity: MEDIUM · Scope: `frontend/src/components/ui/page-header.tsx` (new) + 5 files

Follow-up to 005. That plan unified the page *frame*; this one unifies what sits
at the top of it.

## Problem

Five pages repeat the same title + description block, wrapped in **three
different** row layouts:

- `printers-list.tsx:58` — `flex items-center justify-between` (does not stack on
  mobile; the buttons squash against the title)
- `statistics.tsx:420` — `flex flex-col gap-3 sm:flex-row sm:items-end
  sm:justify-between` (the correct, responsive one)
- `profiles.tsx:7`, `organize.tsx:7`, `settings-panel.tsx:755` — a bare `<div>`

Every one of them uses `<h2>`, so **no page in the app has an `<h1>`** — a real
accessibility defect, not just an inconsistency.

Separately, `printers-list.tsx:63` and `:71` hand-roll a button each
(`px-3 py-2 rounded bg-primary …`) instead of using `Button`. `DESIGN.md` says
"Style buttons *only* through `buttonVariants`" — these are the last two that
don't, and they consequently have no press feedback and no focus ring.

## Step 1 — Create `frontend/src/components/ui/page-header.tsx`

```tsx
import { ReactNode } from "react";

/**
 * The heading row of a standard page: title, optional description, optional
 * actions on the right. Carries the page's one `<h1>`.
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
```

The row layout is Statistics' responsive one — it is the only one of the three
that stacks correctly on a phone. Keep it exactly.

## Step 2 — Convert the five headers

Replace each title/description block (and its wrapper row) with `PageHeader`.
The visible text does not change.

| File | Title | Description | Actions |
| --- | --- | --- | --- |
| `pages/profiles.tsx` | Profiles | Filament and printer presets for cost tracking and slicer defaults | — |
| `pages/organize.tsx` | Catalog | Collections and tags | — |
| `components/settings-panel.tsx` (~755) | Settings | Vault configuration and display preferences | — |
| `pages/statistics.tsx` (~420) | Statistics | Cost, filament and print activity from completed jobs | the existing `<Segmented …>` |
| `components/printers-list.tsx` (~58) | Printers | Connected printer endpoints | the Refresh + Add buttons |

## Step 3 — Printers' buttons through `Button`

In `printers-list.tsx`, import `Button` from `@/components/ui/button` and replace
the two hand-rolled `<button>`s. Keep every behaviour — the `onClick` bodies, the
`disabled` state, the conditional "Sign in to add" label, the icons — and change
only the styling mechanism:

```tsx
actions={
  <>
    <Button variant="outline" size="xs" onClick={() => printersQuery.refetch()}>
      <RefreshCw className="h-3.5 w-3.5" />
      Refresh
    </Button>
    <Button
      size="xs"
      onClick={() => {
        if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
        setAddOpen(true);
      }}
      disabled={!auth.isAuthenticated}
    >
      <Plus className="h-3.5 w-3.5" />
      {auth.isAuthenticated ? "Add printer" : "Sign in to add"}
    </Button>
  </>
}
```

`Button` already provides the icon gap (`gap-1.5`), the disabled opacity, the
press feedback and the focus ring — do not re-add any of them via `className`.
`size="xs"` is `h-8 px-3 text-xs`, the closest match to the current buttons.

## Scope boundaries

- Do not touch the "Add your first printer" button inside `EmptyState`, the
  printer cards, the Segmented control's internals, or any modal.
- Do not restyle anything below the header row on any page.
- Do not change page copy.

## Verify

- `pnpm lint && pnpm typecheck && pnpm test`
- `grep -rn "text-2xl font-bold" src/pages src/components` should return only
  `page-header.tsx`, plus `login.tsx` / `setup.tsx` / `share.tsx` (out of scope —
  they are not standard pages).
- Feel-check at 375px wide: on Printers, the title and the two buttons must
  **stack**, not collide. At 1920px they sit on one row, buttons right-aligned.
- Tab to the Refresh button: it must now show a visible `ring-ring` focus ring
  and dip slightly when pressed.

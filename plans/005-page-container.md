# 005 — One page container, one content width

Commit: `a5933ac` · Severity: HIGH (visible inconsistency) · Scope: `frontend/src/components/ui/page-container.tsx` (new) + 6 pages

## Problem

Six scrolling pages hand-copy the same scroll container and then each pick a
different content width. Printers and Statistics sit side by side in the nav and
look like two different apps: one spans a 2560px farm monitor edge to edge, the
other is a 1024px column with a third of the screen empty.

## The rule being established

- **Standard pages** (a heading + cards/forms) use `PageContainer`. One width:
  `max-w-6xl`, centered.
- **Full-bleed surfaces** opt out entirely and are *not* touched by this plan:
  the vault grid (`model-grid.tsx`), model detail (`model-detail/index.tsx`, a
  split pane), and the public share page (`pages/share.tsx`). These are app
  surfaces, not documents — they legitimately use the whole viewport.
- **Prose** (`document-detail.tsx`) uses `width="prose"` (`max-w-4xl`): a
  reading measure, deliberate.

## Step 1 — Create `frontend/src/components/ui/page-container.tsx`

```tsx
import { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * The standard page frame: the scroll container, its padding (including the
 * mobile bottom-nav clearance), and one canonical content width.
 *
 * Full-bleed surfaces — the vault grid, model detail, the share page — do not
 * use this; they own the whole viewport by design.
 */
export function PageContainer({
  width = "default",
  className,
  children,
}: {
  /** "prose" is a reading measure, for long-form document views. */
  width?: "default" | "prose";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className="h-full overflow-y-auto [scrollbar-gutter:stable] bg-background p-6 pb-24 md:pb-6">
      <div
        className={cn(
          "mx-auto w-full space-y-6",
          width === "prose" ? "max-w-4xl" : "max-w-6xl",
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
```

`[scrollbar-gutter:stable]` comes from `settings.tsx` — it stops the page from
shifting sideways when content grows past the fold. It belongs on all of them.

## Step 2 — Convert the six pages

For each, replace the outer `<div className="h-full overflow-y-auto …">` **and**
its inner `mx-auto max-w-* space-y-6` wrapper with a single `<PageContainer>`.
The page's own content is unchanged. Delete the now-empty nesting — do not leave
a redundant `<div>` behind.

| File | Current outer + inner | Becomes |
| --- | --- | --- |
| `pages/printers.tsx` | `h-full overflow-y-auto bg-background p-6 pb-24 md:pb-6` (no inner wrapper) | `<PageContainer>` |
| `pages/organize.tsx` | same + `<div className="w-full space-y-6">` | `<PageContainer>` |
| `pages/printer-detail.tsx` | `h-full overflow-y-auto p-6 pb-24 md:pb-6` | `<PageContainer>` |
| `pages/profiles.tsx` | same + `mx-auto w-full max-w-5xl space-y-6` | `<PageContainer>` |
| `pages/statistics.tsx` (line ~418) | same + `mx-auto w-full max-w-5xl space-y-6` | `<PageContainer>` |
| `pages/settings.tsx` | same + `mx-auto w-full max-w-6xl` | `<PageContainer>` |

Note `settings.tsx`'s inner wrapper has **no** `space-y-6` today. `PageContainer`
adds it. Check Settings visually after the change: if the tab bar now sits too
far from the heading, pass `className="space-y-0"` on that one page rather than
removing `space-y-6` from the primitive.

`pages/document-detail.tsx` is a flex column with its own `min-h-0` chain
(`h-full flex flex-col` → `flex flex-col flex-1 min-h-0`). **Leave it alone** —
converting it risks breaking the markdown viewer's scroll. It is listed in
DESIGN.md as prose-width for future use, not converted here.

## Scope boundaries

- Do not touch `model-grid.tsx`, `model-detail/`, `share.tsx`, `login.tsx`,
  `setup.tsx`, `not-found.tsx`.
- Do not restyle any page's contents — headings, cards, buttons stay as they are.
- Do not change `app-shell.tsx`.

## Verify

- `pnpm lint && pnpm typecheck && pnpm test`
- `grep -rn "h-full overflow-y-auto" src/pages/` should return only
  `document-detail.tsx` (which uses `h-full flex flex-col`, so likely nothing).
- Feel-check at a wide viewport (≥1920px): Printers, Statistics, Profiles,
  Settings, Organize and a printer detail page must all have their content in a
  column of the same width, starting at the same left edge. Flip between
  Printers and Statistics in the nav — nothing should jump horizontally.

# 002 — Batch toolbar: animate the entrance and exit

Commit: `a5933ac` · Severity: HIGH · File: `frontend/src/components/batch-toolbar.tsx`

## Problem

`if (count === 0) return null;` (line 39). The selection toolbar snaps into and
out of existence with no motion — the most jarring state change in the app. It
is also *rapidly* retriggered (select → deselect → select), so per `DESIGN.md`
rule 5 it must use a **CSS transition** that retargets from its current state,
not a keyframe utility (`slide-up`) that restarts from zero.

## Current code (lines 35–44)

```tsx
  const [moveOpen, setMoveOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  if (count === 0) return null;

  return (
    <>
      <div className="fixed inset-x-0 bottom-4 z-50 flex justify-center px-4 pointer-events-none">
        <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-background/95 px-3 py-2 shadow-lg backdrop-blur">
```

## Target code

Add the import (the file already imports from `react`; `DURATION` /
`useMountTransition` come from `@/lib/overlay`, the same primitives `Modal` and
`DropdownMenu` use):

```tsx
import { DURATION, useMountTransition } from "@/lib/overlay";
```

Replace the early return with a mount transition, and put `data-state` on the
pill:

```tsx
  const [moveOpen, setMoveOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  // Must match the pill's `duration-fast` transition below.
  const { mounted, state } = useMountTransition(count > 0, DURATION.fast);

  if (!mounted) return null;

  return (
    <>
      <div className="fixed inset-x-0 bottom-4 z-50 flex justify-center px-4 pointer-events-none">
        <div
          data-state={state}
          className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-background/95 px-3 py-2 shadow-lg backdrop-blur transition-[opacity,transform] duration-fast ease-out data-[state=closed]:translate-y-2 data-[state=closed]:opacity-0 motion-reduce:data-[state=closed]:translate-y-0"
        >
```

Notes for the executor:

- `count` keeps rendering its last value during the exit (that is correct — the
  pill should fade out reading "3 selected", not "0 selected"). Do **not** add a
  ref to freeze it; do not change any other behaviour.
- 8px rise (`translate-y-2`), not more. Nothing bounces.
- The `motion-reduce:` variant drops the movement and leaves the opacity fade,
  matching `Modal`/`DropdownMenu`.

## Scope boundaries

Do not restyle the buttons inside the toolbar, do not touch the Move/Tag/Delete
modals, do not change selection logic in the parent.

## Verify

- `pnpm lint && pnpm typecheck && pnpm test`
- Feel-check: select a model, deselect, reselect quickly. The pill must reverse
  smoothly mid-flight — never restart or snap.

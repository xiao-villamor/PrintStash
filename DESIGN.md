# PrintStash — design system

Binding rules for the frontend's visual and motion language. Read this before
adding or restyling UI. Terms here are as binding as the domain language in
`CONTEXT.md`.

The system exists to stop drift: before it, the same button lived in a dozen
hand-typed variants and the same easing curve was retyped five slightly
different ways. **Reach for an existing token or primitive first; add a new one
only when nothing fits, and add it as a token, never as a one-off value.**

## Personality

PrintStash is a **productivity app** — a crisp dashboard, not a showy consumer
product. Motion is subtle, fast, and functional. It exists to explain where
something came from, to confirm a press landed, or to soften a jarring swap.
Nothing bounces. Nothing announces itself. If an animation draws attention to
itself, it is wrong.

## Tokens

All tokens are CSS custom properties in `frontend/src/globals.css`, mapped into
Tailwind in `frontend/tailwind.config.cjs`. **Use the Tailwind class, not the raw
variable** — `bg-primary`, not `bg-[var(--primary)]`.

### Color

Every color has a light and a dark value. Primary actions stay blue in both
themes so their meaning does not change with appearance. Never hand-encode a
theme swap (`blue-600 dark:orange-400`) — use the semantic token and it follows
the theme for free.

| Token | Use |
| --- | --- |
| `background` / `foreground` | Page base |
| `card` / `card-foreground` | Raised content surfaces |
| `popover` / `popover-foreground` | Floating panels: menus, pickers |
| `popover-hover` | Perceptible hover/focus fill for items inside floating panels |
| `muted` / `muted-foreground` | Recessed fills, secondary text |
| `primary` / `primary-foreground` | Primary action only. `primary-hover` for hover, `primary-soft` for focus/selection rings |
| `accent` / `accent-foreground` | Tinted hover/selected controls; use together without an additional border or ring |
| `destructive` / `destructive-foreground` | Delete and other irreversible actions |
| `success`, `warning` | Status only — never as decoration |
| `border`, `input`, `ring` | Edges and focus rings |
| `surface-*`, `on-surface*`, `outline*` | The elevation scale, for multi-layer panels |

**Pair a background with its `-foreground`.** `bg-primary` takes
`text-primary-foreground`, which is white in both themes. Keep using the paired
token rather than hardcoding `text-white`, so future palette changes remain
safe.

Focus rings are `ring-ring`. A hardcoded `focus:ring-orange-500` is a bug: it
stays orange in the light theme.

### Action and selection colors

Primary actions and selected controls are deliberately different:

- A command that starts work (`Upload`, `Save`, `Send`) uses
  `bg-primary text-primary-foreground hover:bg-primary-hover` or the default
  `Button` variant.
- A control that represents current state (segmented option, view mode, active
  filter or selected tab) uses `bg-accent text-accent-foreground`.
- Do not put `bg-primary text-primary-foreground` on selected controls. It gives
  navigation the same weight as a primary action.
- Do not add a border or ring to a filled selected control. Its tinted fill is
  the state indicator; outlines are reserved for keyboard focus via
  `focus-visible:ring-ring`.
- Orange belongs to `warning` or explicitly tertiary/status content. It is not
  an action or selection color.

### Radius, type, elevation

- Radius derives from `--radius` (0.375rem): `rounded-sm|md|DEFAULT|lg`. No
  arbitrary `rounded-[6px]`.
- Type ramp adds `text-2xs` (0.6875rem) and `text-3xs` (0.625rem) for the mono
  label style. No `text-[11px]`.
- Layering uses the named scale: `z-dropdown` (50) for menus and popovers,
  `z-overlay` (100) for modals and drawers. No arbitrary `z-[60]`.

## Layout

A page is either a **document** or a **surface**, and the choice is not a matter
of taste.

- **Documents** — a heading plus cards, forms, or charts: Printers, Statistics,
  Profiles, Settings, Organize, printer detail. These compose
  `PageContainer` (`components/ui/page-container.tsx`), which owns the scroll
  container, the padding, the mobile bottom-nav clearance, and **one** content
  width: `max-w-screen-2xl`, centered. There is no second width to choose from, and no
  page may hand-roll its own scroll container — that is exactly how Printers
  ended up spanning a 2560px monitor while Statistics sat in a 1024px column.
- **Surfaces** — the vault grid, model detail's split pane, the public share
  page. These own the whole viewport and do not use `PageContainer`. A grid of
  print models *should* use every pixel of a farm monitor.
- **Prose** — long-form reading (document detail) uses
  `<PageContainer width="prose">` (`max-w-4xl`), a reading measure.

Adding a page means picking one of these three. If a new page seems to need a
fourth width, it doesn't — pick the closest and move on.

A document page opens with `PageHeader` (`components/ui/page-header.tsx`):
title, optional description, optional actions on the right. Do not hand-write
that row.

**Every page has exactly one `<h1>`, and it is the thing the page is about** —
the collection name on the vault, the printer's name on printer detail, the
page title elsewhere. `PageHeader` provides it. The two detail views whose
title carries inline metadata (printer detail's provider/beta badges, model
detail) render their own `<h1>` instead of composing `PageHeader`; that is the
only sanctioned exception, and it does not license a third heading style.

## Motion

### Curves and durations — the only ones that exist

```css
--ease-out:    cubic-bezier(0.16, 1, 0.3, 1);     /* the house curve */
--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);   /* on-screen movement only */
--duration-press: 150ms;
--duration-fast:  200ms;
--duration-slow:  300ms;
```

In Tailwind: `ease-out`, `ease-in-out`, `duration-press|fast|slow`.

**Never type a raw duration or cubic-bezier into a component.** `duration-200`
is a finding — it is `duration-fast`. This is the single rule that keeps the
system from re-fragmenting.

Choosing:

| Situation | Curve | Duration |
| --- | --- | --- |
| Entering or exiting (modal, menu, panel, toast) | `ease-out` | `fast` |
| Moving or morphing on screen (tab indicator) | `ease-in-out` | `fast` |
| Press feedback | `ease-out` | `press` |
| Hover, color change | default | `press` |
| Constant motion (progress fill) | `linear` | n/a |
| Image/content fade-in | `ease-out` | `slow` |

`ease-in` is never correct on UI — it starts slow, delaying exactly the moment
the user is watching. **Nothing in the app may exceed 300ms.**

### Rules

1. **Animate `transform` and `opacity` only.** Animating `width`, `height`,
   `top`, or `margin` triggers layout on every frame. Progress bars use
   `transform: scaleX()`, not `width`.
2. **`transition-all` is banned.** It animates properties you did not intend,
   off the GPU. List them: `transition-[background-color,border-color,transform]`.
   The count in `frontend/src/` must stay at zero.
3. **Never scale from 0.** Nothing in the real world appears from nothing.
   Entrances start at `scale(0.97)` + `opacity: 0`.
4. **Floating panels scale from their trigger**, not their center — a menu opened
   from the top-right corner grows out of that corner
   (`transform-origin: top right`). Modals are the exception: they are centered,
   so `transform-origin: center` is correct there.
5. **Reversible or rapidly-triggered motion uses CSS transitions, not
   keyframes.** Transitions retarget from wherever they are; keyframes restart
   from zero. A drawer whose backdrop is tapped mid-open must reverse smoothly,
   which keyframes cannot do.
6. **Enter and exit are symmetric.** An overlay that keyframes in and then
   vanishes in one frame on close is a bug (`useMountTransition` exists to
   prevent exactly this).

   Its `exitMs` argument and the panel's `duration-*` class are two halves of
   one animation living in two languages, and nothing type-checks them against
   each other. **If the timer is shorter than the transition, the panel is torn
   out of the DOM mid-animation and the close visibly snaps** — the exact bug the
   primitive exists to prevent. Always pass a `DURATION` constant from
   `lib/overlay.ts` and match it to the class the panel actually uses:
   `DURATION.press` ↔ `duration-press`, `DURATION.fast` ↔ `duration-fast`.
7. **High-frequency actions do not animate.** Route navigation happens hundreds
   of times a day — there is deliberately **no page transition**, and none should
   be added. Animation is for occasional events: overlays, state swaps, feedback.
8. **Press feedback on anything pressable**: `active:scale-[0.98]` (cards use
   `0.99`) over `duration-press`. `Button` already does this.

### Reduced motion

`@media (prefers-reduced-motion: reduce)` in `globals.css` degrades every
movement utility to a plain opacity fade. **Reduced motion means less movement,
not less feedback** — the fade stays so the user still sees that something
happened. Any new keyframe utility must be added to that block.

Two traps, both hit in practice:

- The stagger rules are `:nth-child(...)` (specificity 0,2,0). A reduced-motion
  override written as `.stagger-children > *` (0,1,0) **loses on specificity and
  silently does nothing.** Match the specificity.
- Gate hover-only motion behind `@media (hover: hover)`; touch devices fire a
  false hover on tap.

Both are covered by e2e tests in `frontend/tests/e2e/app-routes.spec.ts`.

### Entrance utilities

| Class | Use |
| --- | --- |
| `animate-card-in` | Grid/list items — fade + 4px rise |
| `animate-panel-in` | Tab bodies, section swaps, empty states |
| `pop-in` | Floating panels — from `scale(0.97)` |
| `slide-up`, `slide-in-left` | Drawers and sheets |
| `stagger-children` | On a grid container: children settle 30ms apart, **capped at 10** so a 60-card page still lands inside the 300ms budget |

Stagger is decorative sequencing. It must never delay interaction, and it is
dropped entirely under reduced motion.

## Primitives

In `frontend/src/components/ui/`. **Compose these — do not rebuild them.** Every
bespoke overlay reintroduces the same four bugs (no focus trap, no Escape, no
scroll lock, no exit animation).

| Primitive | Gives you |
| --- | --- |
| `PageContainer` | The standard page frame: scroll container, padding, bottom-nav clearance, the one content width |
| `PageHeader` | The page's heading row: the single `<h1>`, description, right-aligned actions. Stacks on mobile |
| `Button` | All variants/sizes, press feedback, `loading` state. Style buttons *only* through `buttonVariants` |
| `ModalShell` / `Modal` | Portal, focus trap + restore, Escape, scroll lock, symmetric enter/exit |
| `Drawer` | The above, sliding from an edge, interruptible mid-flight |
| `DropdownMenu` | Origin-aware scale/fade, outside-`pointerdown` dismiss, Escape refocuses trigger, arrow-key roving |
| `TabBar` | ARIA `tablist`, arrow-key nav, sliding indicator |
| `Input`, `Checkbox`, `Badge`, `Spinner`, `EmptyState`, `Card`, `Skeleton` | Token-correct defaults |

Supporting hooks in `frontend/src/lib/`:

- `useMountTransition(open, exitMs)` — keeps a node mounted through its exit
  animation. This is what makes close animations possible at all.
- `useOverlayBehavior(...)` — focus trap, Escape, scroll lock, focus restore.
- `useComboboxNav(...)` — listbox ARIA + arrow-key highlight for tag and
  category pickers.

## Checklist for new UI

0. A new page composes `PageContainer` unless it is a full-bleed surface. No
   hand-rolled scroll container, no new content width.
1. Semantic color tokens, paired with their `-foreground`. No `dark:` color
   hand-encoding, no arbitrary `[var(--…)]`.
2. Durations and curves from the token scale. No raw `duration-200`, no
   hand-typed cubic-bezier.
3. Transform/opacity only. No `transition-all`. Explicit property lists.
4. Overlays compose `Modal`/`Drawer`/`DropdownMenu` — never hand-rolled.
5. Pressable things have press feedback; focusable things have a `ring-ring`
   focus ring.
6. New movement is registered in the reduced-motion block, at matching
   specificity.
7. Nothing over 300ms. Nothing bounces.

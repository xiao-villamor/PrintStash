# PrintStash — design system

Binding rules for the frontend's visual and motion language. Read this before
adding or restyling UI. Terms here are as binding as the domain language in
`CONTEXT.md`.

The system exists to stop drift: before it, the same button lived in a dozen
hand-typed variants and the same easing curve was retyped five slightly
different ways. **Reach for an existing token or primitive first; add a new one
only when nothing fits, and add it as a token, never as a one-off value.**

## Personality

PrintStash is a **productivity app** for working with a print library. Its
OpenDesign language is compact and technical: neutral surfaces, fine dividers,
square controls, and restrained blue for actions. Inter carries interface text;
JetBrains Mono is reserved for compact technical values and labels. Motion is
subtle, fast, and functional. It explains a change or confirms an action; it
never turns routine navigation into a performance.

## Tokens

Theme and motion tokens are CSS custom properties in `frontend/src/globals.css`,
mapped into Tailwind in `frontend/tailwind.config.cjs`. Shared depth and spring
curve tokens live in `frontend/packages/ui/src/motion.css` and its UI preset. Cursor tokens and their
named utilities share `frontend/packages/ui/src/cursors.css` (see Cursors below).
**Use the Tailwind class, not the raw
variable** — `bg-primary`, not `bg-[var(--primary)]`.

### Color

Every color has a light and a dark value. The base palette is `#F6F7F9` /
`#0C0F14`, panels are `#FFFFFF` / `#13171E`, and primary actions use
`#4D6F9B` in both themes. Neutral selection uses `#EEF0F3` in light and a
translucent white fill in dark. Use semantic tokens instead of hand-encoding a
theme swap so colors remain consistent throughout the application.

| Token | Use |
| --- | --- |
| `background` / `foreground` | Page base |
| `card` / `card-foreground` | Raised content surfaces |
| `popover` / `popover-foreground` | Floating panels: menus, pickers |
| `popover-hover` | Perceptible hover/focus fill for items inside floating panels |
| `muted` / `muted-foreground` | Recessed fills, secondary text |
| `primary` / `primary-foreground` | Primary action only. `primary-hover` for hover, `primary-soft` for focus/selection rings |
| `accent` / `accent-foreground` | Neutral hover/selected controls; use together without a colored border or ring |
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
  filter or selected tab) uses neutral `bg-accent text-accent-foreground`.
- Do not put `bg-primary text-primary-foreground` on selected controls. It gives
  navigation the same weight as a primary action.
- Do not add a colored border or ring to a filled selected control. Its neutral
  fill is the state indicator; outlines are reserved for keyboard focus via
  `focus-visible:ring-ring`.
- Orange belongs to `warning` or explicitly tertiary/status content. It is not
  an action or selection color.

### Radius, type, elevation

- Standard control and panel radii derive from `--radius` (zero):
  `rounded-sm|md|DEFAULT|lg`. Circular avatars, status dots and count badges
  remain circular where their shape communicates their role.
- Type ramp adds `text-2xs` (0.6875rem) and `text-3xs` (0.625rem) for the mono
  label style. No `text-[11px]`.
- Layering uses the named scale: `z-dropdown` (50) for menus and popovers,
  `z-overlay` (100) for modals and drawers. No arbitrary `z-[60]`.

## Layout

On desktop, a 53px rail keeps primary destinations visible beside the work
area. A compact header carries search and global actions; the library retains
its resizable collection panel. On narrow screens a bottom navigation and its
More drawer replace the rail. Labels remain available to assistive technology
for icon-only destinations, and keyboard focus is always visible.

A page is either a **document** or a **surface**, and the choice is not a matter
of taste.

- **Documents** — a heading plus cards, forms, or charts: Printers, Statistics,
  Profiles, Settings, printer detail. These compose
  `PageContainer` (`components/ui/page-container.tsx`), which owns the scroll
  container, the padding, the mobile bottom-nav clearance, and **one** content
  width: the full available work area, with 24px desktop padding. There is no second width to choose from, and no
  page may hand-roll its own scroll container. The approved workbench keeps
  document pages aligned to the same full-width frame on large monitors.
- **Surfaces** — the vault grid, model detail's split pane, the public share
  page. These own the whole viewport and do not use `PageContainer`. A grid of
  print models *should* use every pixel of a farm monitor.
- **Search results** are a browsing surface too. Keep the result grid at the
  surface width, put everyday filters and view controls beside the results, and
  let each result carry its own visual boundary. Do not wrap the grid in a
  document-width card or hide result-type controls behind a disclosure.
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

### Framed work surfaces

Primary settings sections and the current state needed to use them stay visible.
Do not put a whole feature, its enable control, or its safety status inside a
`details` dropdown. Use disclosure only for genuinely optional supporting
information; when several technical views need separation, use the existing
`TabBar` and keep the everyday controls outside the tabs. Prefer aligned text
for a small set of related status values instead of a row of decorative badges.
For current storage, lead with the provider and one active safety state. Keep
paths readable but secondary; explain migration in its workflow, not in the
status card. Similar candidates belong in each Model's Similar tab. The library-wide Similar
models page, New collection and Select actions stay visible beside Filters in
the library toolbar. On narrow panes, the actions wrap within the same toolbar.
Keep these actions out of the sidebar and global header. Keep Model detail
tabs in one row, with horizontal scrolling only when their labels cannot fit.
Async settings cards keep their header and reserve the shape of their content
with skeletons until initial reads settle. Do not expose a partial form whose
provider fields appear later. Remote storage uses the same visible category and
provider choices as Move Vault storage; keep selected state and field framing
consistent between the two flows.
Collection tree counts describe the whole branch, including child collections.
Keep count badges visible at the default sidebar width and truncate long names
before allowing a badge to slide outside the viewport.
Show storage capacity summaries and editable limits in human units such as MB
or GB. Convert edits to bytes at the API boundary. Keep precise B/KB values
only where a small file's exact size helps a technical task.
Format stored durations as hours and minutes in filter summaries; keep the input
unit explicit wherever users enter a number.

Dense document workflows such as Profiles, Settings, and Pending Imports use a
shared visual grammar: **one framed work surface**, not a collection of loose
status cards. The frame keeps controls, context, rows, loading, and empty states
visually attached to the same task.

Compose the pattern from existing primitives:

1. Start with `Card className="overflow-hidden"` below the `PageHeader`.
2. Put local tabs, filters, and batch actions in a compact toolbar using
   `border-b bg-muted/30 p-3`. `TabBar` uses an inset background frame and
   neutral `accent` for the active state; commands remain regular `Button` variants.
3. Introduce the current section with `border-b px-4 py-4 sm:px-5`, a square
   `h-8 w-8 rounded-md bg-muted` icon anchor, a `text-sm font-semibold` title,
   and a `text-xs text-muted-foreground` description.
4. Render related records as dense rows inside that frame, separated by
   `divide-y divide-border`. A row may use a small state icon, primary text,
   compact metadata, a status badge, and contextual actions aligned right.
5. Keep loading skeletons and empty states inside the same frame. The surface
   must not disappear or change width when its data state changes.

The frame is the main elevation boundary. Do not wrap every queue row, setting,
or result in another shadowed `Card`; that creates a generic card feed and loses
the app's compact tool-panel hierarchy. Nested bordered groups are appropriate
only when they represent a real sub-form, table, or independently editable
unit.

Use mono uppercase micro-labels for technical fields and table-like headers,
not for primary titles or prose. Keep primary blue for actions that start work;
status and selection use their semantic tokens. On narrow screens, rows stack
into a readable content band and a separate action band. Text gets `min-w-0`
and may wrap; actions must remain reachable without horizontal scrolling.

## Cursors

Ordinary web actions and unavailable controls use the arrow cursor.
The shared policy in `frontend/packages/ui/src/cursors.css` owns
`--cursor-action` and `--cursor-disabled`, and is imported by both the app and
the UI package's stylesheet. Native buttons, links, disclosure controls and ARIA
widgets receive it automatically, including their icon and label content.

Use `cursor-action` for clickable labels or cards that need an explicit
affordance and `cursor-disabled` for unavailable custom surfaces. Never add
`cursor-pointer`, `cursor-default`, `cursor-not-allowed`, or inline equivalents
in a component. Disabled labels follow their contained control. Keep text
editing, drag (`cursor-grab`/`cursor-grabbing`) and resize cursors where those
operations actually apply. The source guard and browser hover suite prevent
ordinary action cursors from drifting between components.

## Motion

### Curves and durations — the only ones that exist

```css
--ease-out:    cubic-bezier(0.16, 1, 0.3, 1);     /* the house curve */
--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);   /* on-screen movement only */
--duration-press: 150ms;
--duration-fast:  200ms;
--duration-slow:  300ms;
/* Loading preview handoffs preserve the existing card surface. */
--ease-library-enter: cubic-bezier(0.2, 0.6, 0.35, 1);
--duration-library-enter: 240ms;
--scale-content-enter: 0.98;
/* --ease-content-settle is the sampled curve in packages/ui/src/motion.css. */
```

Use three related behaviors: edge movement for drawers, a small opaque depth settle
for section changes, and opacity-only handoffs for loading surfaces. Do not invent
new directions, bounce, cascades, or text crossfades for individual features.

Collections, Search, Favorites, Pending, Saved views and Settings use one shell
sidebar and one persisted width (`--workbench-sidebar-width`, 320px by default,
resizable from 280px to 520px). The model inspector uses the same width on desktop.
Section navigation preserves the sidebar's open state, boundary and workspace
position. Settings navigation occupies this frame rather than adding another column.
The border and background belong to the stationary shell, outside animated content.

Model detail tabs, Settings sections and Upload modes share
`useContentMotion`: content commits immediately at full opacity, then settles from
`--scale-content-enter` to its natural size, anchored at the top center. The
critically damped spring uses the fast token as a 200ms response and settles in
about 300ms, without overshoot. Rapid selections inherit the current presentation
and velocity. Initial mount and data refreshes do not replay section motion.
The frame remains mounted; reduced motion makes selection immediate and static.
Tab press feedback begins on pointer-down and underline feedback stays local.

Search, Favorites and Settings main destinations use this same depth spring. The
route outlet keeps one mounted content surface; its readiness signal lives inside
Suspense so even a cold destination starts motion when its content commits. Sidebar
navigation and the top bar sit outside that surface and remain stationary during
these switches. Search in the rail opens the main Search destination without
changing the sidebar's open preference. Back/Forward uses the same treatment.
Typing, filters, Settings sub-sections and data refreshes do not replay the main
transition; internal controls retain their own local feedback. First load is static.

Library card entrances use the same depth range and sampled spring curve, together,
without directional travel or staggering. Resolving a loading grid instead keeps
frames opaque: text placeholders dissolve over content and decoded images fill
the fixed preview. Loading cards and populated cards share geometry. If collection
metadata moves the destination grid, cards continue from visible placeholder
positions; reduced motion omits that translation. Missing-preview icons appear
only for missing or failed assets. Counts update in place without motion.

The Library model inspector is a solid, non-modal surface docked to the right.
It enters from beyond that edge and returns along the same path, including on
mobile. A critically damped spring is sampled into compositor keyframes so preview
initialization does not drive its frame loop. It uses the fast token as its response; reversing
open/close preserves its current displacement and velocity. Hide only when the
spring reaches the closed position, never on a fixed exit timer. Switching Models
keeps the panel stationary. Closed panels become inert immediately. Reduced motion
uses immediate visibility changes and retains the existing focus handoff.

The left Collections pane uses a smooth cubic ease into rest over the slow timing,
with zero starting/ending velocity at rest and inherited velocity on interruption.
It has no spring response or oscillation. Its shell column releases and restores space continuously; the inner outliner keeps its intrinsic width,
clipped and anchored to the moving right boundary. This deliberately animates one
layout width so the sidebar and workspace cannot drift onto separate timelines.
Retain the outliner DOM, filter, expansion and scroll state while closed; make it
inert immediately and hide its contents only at rest. Drag resizing remains 1:1,
initial page layout does not replay entry, and reduced motion settles immediately.
Local sidebar tool changes use `useContentReveal`: stationary geometry with opacity
0.86→1 over the library timing. Cross-route navigation keeps the sidebar static.
Reduced motion makes these reveals immediate.

Source, History and Similar loading states use quiet row placeholders aligned to
their content. Delay placeholders by 200 ms to avoid flashing on fast reads; cached
content remains visible during background reads. Real content appears fully opaque
as soon as it is ready; only the placeholder cover clears over the 150 ms press
timing. Reserve the section footprint, and omit pulse or spatial movement.

Mobile drawers use the same sampled settle profile over the slow timing token;
their presence timer matches that timing. Floating menus and modals use the same
0.98 depth range with a brief opacity handoff.

In Tailwind: `ease-out`, `ease-in-out`, `ease-settle`, `duration-press|fast|slow`.

**Never type a raw duration or cubic-bezier into a component.** `duration-200`
is a finding — it is `duration-fast`. This is the single rule that keeps the
system from re-fragmenting.

Choosing:

| Situation | Curve | Duration |
| --- | --- | --- |
| Entering or exiting (modal, menu, panel, toast) | `ease-out` | `fast` |
| Content/section settle | critical spring / `ease-settle` | 200ms response / `slow` |
| Press feedback | `ease-out` | `press` |
| Hover, color change | default | `press` |
| Constant motion (progress fill) | `linear` | n/a |
| Image/content fade-in | `ease-out` | `slow` |

`ease-in` is never correct on UI — it starts slow, delaying exactly the moment
the user is watching. **Nothing in the app may exceed 300ms.**

### Rules

1. **Animate `transform` and `opacity` only.** Animating `width`, `height`,
   `top`, or `margin` triggers layout on every frame. Progress bars use
   `transform: scaleX()`, not `width`. The docked sidebar is an explicit exception:
   one width animation keeps its boundary and the workspace on the same timeline.
2. **`transition-all` is banned.** It animates properties you did not intend,
   off the GPU. List them: `transition-[background-color,border-color,transform]`.
   The count in `frontend/src/` must stay at zero.
3. **Never scale from 0.** Nothing in the real world appears from nothing.
   Floating entrances start at `scale-content-enter` + `opacity: 0`; section
   content stays opaque.
4. **Floating panels scale from their trigger**, not their center — a menu opened
   from the top-right corner grows out of that corner
   (`transform-origin: top right`). Modals are the exception: they are centered,
   so `transform-origin: center` is correct there.
5. **Retarget from the current presentation.** Interactive drawer and section
   springs preserve both position and velocity. Never restart a spring from its
   initial state or lock input while it runs. Non-gesture CSS transitions retarget
   from the current computed value.
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
7. **High-frequency actions do not replay page motion.** Search, Favorites and
   Settings destination changes are the scoped workbench exception described
   above. Other route navigation stays immediate. Typing, query changes and data
   updates never animate the whole page.
   Commit a folder's description with its browsing result, so it cannot arrive
   afterward and push the cards down. Clamp long descriptions before the first
   paint. Cached library thumbnails stay visible when their cards remount.
8. **Press feedback on anything pressable**: `active:scale-[0.98]` (cards use
   `0.99`) over `duration-press`. `Button` already does this.

### Reduced motion

`@media (prefers-reduced-motion: reduce)` in `globals.css` removes section and
card movement. Native spring hooks settle immediately. Loading surfaces may retain
brief opacity feedback; selection, focus and active states remain immediate.
Match the full animation selector's specificity, and gate hover-only motion behind
`@media (hover: hover)`.

### Entrance utilities

| Class | Use |
| --- | --- |
| `animate-card-in`, `animate-panel-in`, `animate-card-arrive`, `results-settle` | Shared opaque depth settle for newly inserted content; use the hook for persistent, interruptible section bodies |
| `pop-in` | Floating panels, from the shared 0.98 scale with brief opacity feedback |
| `folder-in-forward`, `folder-in-back`, `slide-up`, `slide-in-left`, `pane-in` | Compatibility names for the same content settle; no directional movement |
| `animate-count-up`, `animate-count-down` | Compatibility names; counts update without animation |
| `stagger-children` | Compatibility name; children now arrive together without delay |

Do not combine a section hook with entrance utilities on its nested content.
Actual drawers use the shared edge motion, not an entrance utility.

**Entrance classes live only for their entrance.** The Library grid is
virtualized: rows and cards mount again whenever the grid reflows (the pane or
a column count changes) or scrolls back into view. A `folder-cards-*`,
`results-settle` or `animate-card-arrive` class left in place replays on each
of those mounts, so content the reader has already seen slides in again. Apply
them through `useEntranceWindow` (`lib/entrance-window.ts`), which releases the
class once its entrance has played.

The Library details panel (`.library-model-inspector`) is one panel for every
Model it shows: it eases in 12px from its edge on open (`fast`), leaves the
same way on close (`press`, kept mounted by `useMountTransition`), and swaps
its contents in place with a `fade-in` when pointed at another Model.

## Primitives

In `frontend/src/components/ui/`. **Compose these — do not rebuild them.** Every
bespoke overlay reintroduces the same four bugs (no focus trap, no Escape, no
scroll lock, no exit animation).

**Browser-native dialogs are banned.** Never call `window.prompt`,
`window.alert`, or `window.confirm`. They bypass PrintStash styling, focus,
motion, accessibility, and error handling. Compose `Modal`, `ConfirmModal`, or
another `components/ui/` primitive instead. CI scans frontend source and keeps
these calls at zero.

| Primitive | Gives you |
| --- | --- |
| `PageContainer` | The standard page frame: scroll container, padding, bottom-nav clearance, the one content width |
| `PageHeader` | The page's heading row: the single `<h1>`, description, right-aligned actions. Stacks on mobile |
| `Button` | All variants/sizes, press feedback, `loading` state. Style buttons *only* through `buttonVariants` |
| `ModalShell` / `Modal` | Portal, focus trap + restore, Escape, scroll lock, symmetric enter/exit |
| `Drawer` | The above, sliding from an edge, interruptible mid-flight |
| `DropdownMenu` | Origin-aware scale/fade, outside-`pointerdown` dismiss, Escape refocuses trigger, arrow-key roving |
| `TabBar` | ARIA `tablist`, arrow-key nav, sliding indicator |
| `Switch` | The one binary toggle: square 36×20 track, On/Off written as text |
| `Input`, `Checkbox`, `Badge`, `Spinner`, `EmptyState`, `Card`, `Skeleton` | Token-correct defaults |

`Button` variants: `default` is the one filled primary action of a group,
`outline` every other action, and `danger` an irreversible action offered
beside ordinary ones (neutral outline, label in `destructive`). The filled
`destructive` variant is reserved for the confirm button inside a destructive
dialog. Buttons carry text, not decorative leading icons, and are never
uppercase.

**Settings** composes one more layer from `frontend/src/components/`:
`settings-card.tsx` (`SettingsCard`, `SettingsGroupHead`, `SettingsSwitch`,
`SettingsNotice`, `SettingsStatus`) and `settings-controls.ts`
(`SETTINGS_BUTTON_PRIMARY`, `SETTINGS_BUTTON`, `SETTINGS_BUTTON_DANGER`,
`SETTINGS_FIELD`). A Settings panel uses these instead of its own header,
toggle, callout, status chip or button classes: every button is 34px/13px,
statuses are aligned text, and notices are neutral panels whose tone is only a
small icon. Rows place label and help on the left and the control on the
right (`settings-preference-row`); lists use `settings-list-row`.

**Single-choice dropdowns are native `<select>`.** `globals.css` gives every
`<select>` without `multiple`/`size` the customizable-select menu (`appearance:
base-select`): the same square popover, 12px rows, neutral hover and trailing
check as `DropdownMenu`, plus a muted chevron on the trigger. Style the trigger
with size and border classes only; do not add `appearance: none`, a hand-drawn
chevron or a custom listbox. Browsers without customizable select show their
native list. Use `DropdownMenu` for actions or rich rows, not to re-implement a
plain value choice.

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
   Browser-native `prompt`/`alert`/`confirm` calls also stay at zero.
5. Pressable things have press feedback; focusable things have a `ring-ring`
   focus ring.
6. New movement is registered in the reduced-motion block, at matching
   specificity.
7. Nothing over 300ms. Nothing bounces.
8. Dense workflows use one framed work surface. Related rows share its frame,
   and loading and empty states remain inside it instead of becoming loose page
   content.

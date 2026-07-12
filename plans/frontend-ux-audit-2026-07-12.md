# PrintStash — UX Design Audit

**Scope:** `frontend/src/` — evaluation only, no files changed.
**Source reviewed:** `index.html`, `root-layout.tsx`, `router.tsx`, `globals.css`,
`components/app-shell.tsx`, `top-bar.tsx`, `bottom-nav-bar.tsx`, `model-card.tsx`,
`model-grid.tsx`, `batch-toolbar.tsx`, `settings-panel.tsx`, `upload-modal.tsx`,
`printers-list.tsx`, `toaster.tsx`, all of `components/ui/`, `lib/toast.ts`,
`lib/errors.ts`, `pages/login.tsx`, `pages/statistics.tsx`, `pages/not-found.tsx`,
`pages/share.tsx`, plus greps across every file in `frontend/src` for
design-token, ARIA, and destructive-action patterns.
**Interface type:** Self-hosted productivity dashboard / asset library (vault grid,
detail views, admin settings, printer control).
**Benchmark:** the 15 usability principles, plus `DESIGN.md`, which is binding for
this repo and which several findings below cite directly.

### How to read this report

Findings are rated 0–4 (4 = users can't complete the task, 1 = cosmetic). Each one
names the principle it violates, the file and line, what a real user experiences,
and the fix. Most impactful first.

## Summary

| Severity | Count |
| --- | --- |
| 4 — Catastrophe | 0 |
| 3 — Major | 5 |
| 2 — Minor | 12 |
| 1 — Cosmetic | 4 |
| **Total** | **21** |

No catastrophes: every destructive action is confirmed, every async path has
feedback, and the overlay primitives are genuinely well built. The major findings
cluster in two places — the **admin settings forms** and **error surfacing**.

## Quick wins

1. **Toast that just says "unknown"** (Severity 3) — add a fallback message in
   `getErrorMessage` so a dropped connection reads "Couldn't reach the server."
2. **Disabled buttons that look enabled** (Severity 3) — the hand-rolled vault
   buttons are missing `disabled:` classes; routing them through `Button` fixes it.
3. **Error banner invisible in dark mode** (Severity 3) — one hardcoded light-red
   block in the grid; swap to `destructive` tokens.
4. **The `/` hint in the search box does nothing** (Severity 2) — either add the
   key handler or drop the hint.

---

## Findings

### [Severity 3] Network and unmapped errors surface as a toast titled "unknown"

- **Principle:** 9 — Error Recovery; 2 — Match Between System and Real World
- **Location:** `frontend/src/lib/errors.ts:157` (`userMessage`), consumed by
  `frontend/src/lib/toast.ts:25` (`toast.error`)
- **Issue:** `parseApiError` returns the code `"unknown"` for anything that isn't an
  `HTTP <status>: <body>` envelope or a snake_case token — which is exactly what a
  `fetch` failure produces (`TypeError: Failed to fetch`). `getErrorMessage("unknown")`
  finds no entry in `ERROR_MESSAGES` and falls through to `code.replace(/_/g, " ")`,
  so the toast title is the literal word **"unknown"**. Any unmapped server code is
  likewise printed raw (`collection_locked` → "collection locked").
- **User impact:** The most common real-world failure on a self-hosted app — the
  backend container is down, the reverse proxy hiccups, Wi-Fi drops mid-upload —
  produces a red toast reading "unknown" with no cause and no recovery step. Users
  retry blindly or assume their data was lost.
- **Fix:** Add `unknown: "Something went wrong reaching the server. Check that
  PrintStash is running and try again."` to `ERROR_MESSAGES`, and give the
  snake_case fallback a sentence-case wrapper rather than a bare de-underscored
  token. Separately, detect `TypeError`/`AbortError` in `parseApiError` and map them
  to a distinct `network_unreachable` code with an actionable message.

### [Severity 3] Disabled buttons in the vault look fully enabled

- **Principle:** 11 — Affordances and Signifiers; 1 — Visibility of System Status
- **Location:** `frontend/src/components/model-grid.tsx:577-584` ("New collection")
- **Issue:** The button carries `disabled={!canAdminSelectedCollection}` but its
  hand-written `className` has no `disabled:` styling — no dimming, no cursor change.
  (The neighbouring "Upload" button at :585 *does* have
  `disabled:opacity-50 disabled:cursor-not-allowed`, so the two disabled states in
  the same row look completely different.) `DESIGN.md` says to style buttons only
  through `buttonVariants`; both of these bypass the `Button` primitive, which is
  precisely why they drifted apart.
- **User impact:** A non-admin in a collection they can only edit sees a normal,
  inviting "New collection" button, clicks it, and nothing happens. No dimming, no
  tooltip, no toast. It reads as a broken app rather than a permission boundary.
- **Fix:** Replace both hand-rolled buttons with `<Button variant="outline" size="xs">`
  / `<Button size="xs">`, which already carry `disabled:opacity-50` and
  `disabled:pointer-events-none`. Add a `title` explaining *why* it's disabled
  ("Admin access required for this collection") so the constraint is legible.

### [Severity 3] Admin settings inputs and selects have no labels

- **Principle:** 13 — Accessibility; 6 — Recognition Over Recall
- **Location:** `frontend/src/components/settings-panel.tsx:880-901` (create-user:
  Username / Email / Initial password), `:1016-1054` (collection access: user /
  collection / role selects), `:1131`, `:1404`, `:1581`. One `<label>` exists in the
  whole 1745-line file.
- **Issue:** The create-user form is three placeholder-only `<input>`s; the
  collection-access row is three `<select>`s whose only clue is a first
  `<option>` ("Select user"). No `<label htmlFor>`, no `aria-label`.
- **User impact:** Screen-reader users hear "edit text, blank" three times and cannot
  tell which field is which. Sighted users lose the field name the moment they start
  typing (placeholders vanish on input) — mid-form, they can't verify whether they put
  the email in the email box. The `<select>`s are read as unlabelled comboboxes.
- **Fix:** Add a visible `<label>` above each control (the codebase already has the
  house style for it — `font-mono text-3xs uppercase tracking-wider text-muted-foreground`,
  as used in `login.tsx:65`). These are the admin flows where a mistake creates the
  wrong user or grants the wrong person admin on a collection; they deserve labels
  more than anywhere else in the app.

### [Severity 3] "Create user" silently disables itself with no stated rule

- **Principle:** 5 — Error Prevention; 10 — Help and Documentation
- **Location:** `frontend/src/components/settings-panel.tsx:905`
- **Issue:** `disabled={usersBusy === "create" || !newUsername.trim() || newUserPassword.trim().length < 8}`.
  The 8-character minimum is enforced only by the disabled state. Nothing on screen
  states the rule, there is no `minLength` on the input, and no inline validation
  message appears.
- **User impact:** The admin fills in all three fields, the Create button stays greyed
  out, and there is no explanation. This is a dead end — the classic "the form won't
  submit and won't tell me why". They will retype the username, reload the page, or
  give up.
- **Fix:** State the constraint under the password field ("At least 8 characters") and
  add `minLength={8}` to the input. Better still, let the button stay enabled and show
  an inline error on submit — a disabled button can never explain itself.

### [Severity 3] The grid's error banner is a light-mode-only red

- **Principle:** 13 — Accessibility (contrast); 4 — Consistency and Standards
- **Location:** `frontend/src/components/model-grid.tsx:721`
- **Issue:** `border-red-200 bg-red-50 p-3 text-sm text-red-700` with no `dark:`
  variant, on a page whose background is `bg-background` (near-black in dark mode).
  `DESIGN.md` names exactly this pattern — hand-encoded palette colors instead of the
  `destructive` token — as a bug, because the token already carries both themes.
- **User impact:** In dark mode (the default for a large slice of this audience) the
  banner renders as a near-white block with light red text — a glaring contrast
  failure, and the one element on screen that is supposed to be readable *because*
  something went wrong is the hardest one to read.
- **Fix:** `rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive`.
  The same hardcoded-red pattern recurs in `batch-toolbar.tsx:77`,
  `top-bar.tsx:288`, and `bottom-nav-bar.tsx:260` — worth a sweep.

---

### [Severity 2] The `/` keyboard hint in the search box is a lie

- **Principle:** 11 — Affordances and Signifiers; 7 — Flexibility and Efficiency
- **Location:** `frontend/src/components/top-bar.tsx:86`
- **Issue:** A `/` key badge is rendered inside the search field — the universal
  "press slash to focus search" signifier. There is no `keydown` listener for `/`
  anywhere in the codebase.
- **User impact:** A power user (the exact person this badge is aimed at) presses `/`,
  a literal slash character lands in whatever field has focus, and nothing focuses.
  Worse than not offering the shortcut: it teaches a gesture that doesn't work.
- **Fix:** Add a window-level `keydown` handler that focuses the input on `/` when the
  event target isn't already an input/textarea/contenteditable — about eight lines. Or
  delete the badge.

### [Severity 2] Destructive actions are split between `window.confirm` and `ConfirmModal`

- **Principle:** 4 — Consistency and Standards; 3 — User Control and Freedom
- **Location:** native `confirm()` in `document-browser.tsx:71`,
  `taxonomy-manager.tsx:508`, `model-detail/revisions-tab.tsx:89`,
  `printers-list.tsx:48`, `printer-detail.tsx:303` and `:352`,
  `model-detail/index.tsx:261` — while `settings-panel.tsx` and `batch-toolbar.tsx`
  use the house `ConfirmModal`.
- **Issue:** Seven destructive paths use the browser's native dialog. It ignores the
  theme, ignores the design system, cannot say "this moves to trash and can be
  restored" the way `ConfirmModal` does, and Chrome lets users tick "prevent this page
  from creating more dialogs" — after which deletes fire with **no confirmation at all**.
- **User impact:** Deleting a printer looks and feels like a different application than
  deleting a model. Users who suppressed the dialog once lose the safety net silently.
- **Fix:** Route all seven through `ConfirmModal`, which already exists, already
  matches the theme, and already supports a `busy` state and a described consequence.

### [Severity 2] The current page is never announced — no `aria-current`, no document title

- **Principle:** 13 — Accessibility; 1 — Visibility of System Status
- **Location:** `frontend/src/components/bottom-nav-bar.tsx:125-146` (`NavTab`),
  `top-bar.tsx:262-283` (`ProfileMenu`); no `document.title` assignment exists anywhere
  in `frontend/src`.
- **Issue:** Active nav state is conveyed purely visually (a filled pill, a colour
  change). No `aria-current="page"`. And because this is an SPA with a single static
  `<title>PrintStash</title>` in `index.html`, the tab title and the screen-reader
  page announcement never change across routes.
- **User impact:** Screen-reader users navigating from Vault to Settings get no
  confirmation that anything happened. Sighted users with several tabs open see five
  identical "PrintStash" tabs and can't tell which is the printer they were watching.
- **Fix:** Add `aria-current={active ? "page" : undefined}` to both nav components, and
  set `document.title` per route (a four-line `useEffect` in each page, or one hook in
  `AppShell` keyed off `pathname`).

### [Severity 2] Inline form errors are not announced and not tied to their fields

- **Principle:** 9 — Error Recovery; 13 — Accessibility
- **Location:** `frontend/src/pages/login.tsx:114-116`
- **Issue:** The login error renders as a plain `<div className="text-xs text-error font-mono">`.
  No `role="alert"`, no `aria-live`, no `aria-invalid` on the username/password inputs,
  no `aria-describedby` linking the message to them. The `Input` primitive already
  supports `aria-[invalid=true]` styling (`ui/input.tsx:6`) — the login page doesn't
  use `Input` and so doesn't get it.
- **User impact:** A screen-reader user submits wrong credentials, hears nothing, and
  is left wondering whether the button worked. Sighted users get a `text-xs` line —
  the smallest text on the page — for the single most important message on it.
- **Fix:** `role="alert"` on the error container, `aria-invalid` + `aria-describedby`
  on both inputs, and bump the message to `text-sm`. Sonner already handles `aria-live`
  for toasts, so this gap is limited to the hand-rolled inline errors.

### [Severity 2] 9-pixel text throughout the card and printer UI

- **Principle:** 14 — Perceptibility; 8 — Aesthetic and Minimalist Design
- **Location:** `model-card.tsx:92` (metric abbreviations), `:201` ("On printer" badge),
  `:969`; `printers-list.tsx:128`; `gcode-viewer.tsx:429,440`;
  `filament-profiles-card.tsx:481`; `settings-panel.tsx:1470`
- **Issue:** `text-[9px]` — below the 0.7rem floor for legible metadata, and an
  arbitrary value where `DESIGN.md` provides `text-3xs` (0.625rem / 10px) precisely so
  this doesn't get hand-typed. Several of these are also uppercase + `tracking-wider`,
  which pushes legibility down further.
- **User impact:** The LYR / TIME / WGT labels on every card — the data users came for
  — are effectively unreadable without leaning in, and impossible on a phone at arm's
  length. Anyone over about 40 will squint at the entire vault grid.
- **Fix:** Replace every `text-[9px]` with `text-3xs`. If 10px still feels heavy in the
  metric grid, the answer is fewer metrics, not smaller type.

### [Severity 2] Focus rings hand-encode the theme swap

- **Principle:** 4 — Consistency and Standards; 13 — Accessibility
- **Location:** `frontend/src/components/printers-list.tsx:255` and `:270` —
  `focus:ring-1 focus:ring-blue-600 dark:focus:ring-orange-500`
- **Issue:** `DESIGN.md` calls this out by name: focus rings are `ring-ring`, and a
  hardcoded ring color is a bug. These two are the only places in the app that
  reimplement the light/dark primary swap by hand, and they use `ring-1` where every
  other focusable element uses `ring-2` with an offset.
- **User impact:** Keyboard users get a visibly thinner, differently-coloured focus ring
  on these two printer fields than anywhere else — the sort of inconsistency that makes
  people doubt they've actually focused the right thing.
- **Fix:** `focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2`,
  or simply use the `Input` primitive.

### [Severity 2] A hover-only "⋮" that isn't a button

- **Principle:** 11 — Affordances and Signifiers (false affordance)
- **Location:** `frontend/src/components/model-grid.tsx:986-988` (`ModelListRow`)
- **Issue:** A `MoreVertical` icon fades in on row hover (`opacity-0 group-hover:opacity-100`)
  but is a bare `<svg>` inside a `<span>` — no button, no handler, no menu. Clicking it
  navigates to the model (it's inside the row `<Link>`).
- **User impact:** Every user who has ever used a file manager will hover, see the
  overflow icon appear, click it expecting a context menu, and instead get yanked into
  the detail page. This is a promise the UI doesn't keep.
- **Fix:** Either wire it to a real `DropdownMenu` (move / tag / delete — the actions
  already exist in `BatchToolbar`), or delete the icon. Half an affordance is worse
  than none.

### [Severity 2] Batch selection is desktop-only

- **Principle:** 7 — Flexibility and Efficiency
- **Location:** `frontend/src/components/model-grid.tsx:599` — the Select button is
  `hidden md:flex`
- **Issue:** Multi-select, and therefore batch move/tag/delete, is unreachable below the
  `md` breakpoint. The `BatchToolbar` itself is mobile-friendly (a floating pill), so the
  capability exists — only its entry point is hidden.
- **User impact:** A user tidying their library from a tablet or phone must open and
  delete models one at a time. For a "clean up 40 imports" task, that's the difference
  between a minute and twenty.
- **Fix:** Show the Select button on mobile too, or use long-press on a card to enter
  select mode (the native pattern users already expect from photo galleries).

### [Severity 2] Model names are truncated with no way to see the full name

- **Principle:** 6 — Recognition Over Recall
- **Location:** `frontend/src/components/model-card.tsx:210-212` — `truncate` +
  `uppercase` on the card title, no `title` attribute
- **Issue:** Card titles are hard-truncated at one line and uppercased (which widens
  text, so they truncate sooner). There is no tooltip or expansion.
- **User impact:** Two models named `bracket-v3-left-reinforced` and
  `bracket-v3-left-revised` render identically as `BRACKET-V3-LEFT-REIN…`. Users must
  open each one to tell them apart — in the exact view whose purpose is telling them
  apart.
- **Fix:** Add `title={model.name}` (a one-attribute fix), and consider `line-clamp-2`
  so a second line is available before truncation kicks in.

### [Severity 2] Filtered empty state offers no way out

- **Principle:** 3 — User Control and Freedom; 6 — Recognition Over Recall
- **Location:** `frontend/src/components/model-grid.tsx:727-746`
- **Issue:** When filters produce zero results the empty state says "Try clearing some
  filters" and deliberately renders **no action** (the `action` prop is `undefined`
  unless there are no filters at all). It also doesn't say *which* filters are active —
  and on mobile the filter panel is behind a drawer.
- **User impact:** Users land on an empty grid, can't see what's filtering it (a tag
  selected three screens ago, a printer-presence filter), and have to hunt through the
  sidebar to undo it. The most common cause of "the app lost my models".
- **Fix:** Render the active filters as removable chips above the grid, and give this
  empty state a "Clear all filters" button.

### [Severity 2] Toast descriptions leak HTTP status codes

- **Principle:** 2 — Match Between System and Real World
- **Location:** `frontend/src/lib/toast.ts:28-32`
- **Issue:** Every API error toast attaches `HTTP ${api.status}` as its description.
- **User impact:** "This model no longer exists." followed by "HTTP 404" tells the user
  nothing they can act on and reads as a leaked implementation detail. Self-hosters do
  benefit from a status code when filing a bug — but it belongs in the console or a
  copyable detail, not in the primary toast copy.
- **Fix:** Drop the status from the toast body; `console.debug` the full `ApiError`
  instead so it's there when someone opens devtools to file an issue.

### [Severity 2] Charts distinguish five categories by colour alone

- **Principle:** 13 — Accessibility (colour-only information); 14 — Perceptibility
- **Location:** `frontend/src/pages/statistics.tsx:55` (`BAR_COLORS`), used at `:360`
  and `:400`
- **Issue:** The "Top collections" and "Most used filaments" bars cycle through five
  hardcoded hexes with no legend and no pattern/label redundancy. Category names *are*
  on the Y axis, which softens this considerably — but the colours themselves carry no
  meaning and are not consistent between the two charts (the same collection can be blue
  in one and pink in the other).
- **User impact:** Colour-blind users lose nothing (the axis labels carry the data), but
  everyone loses the *implied* mapping the colours suggest and doesn't deliver. It reads
  as decoration masquerading as encoding.
- **Fix:** Use a single accent for both charts (these are single-series bar charts — they
  don't need a categorical palette), or make the palette a stable per-category mapping and
  add a legend.

### [Severity 2] Bottom nav locks body scroll on top of the Drawer that already does

- **Principle:** 4 — Consistency and Standards
- **Location:** `frontend/src/components/bottom-nav-bar.tsx:74-79` — a `useEffect`
  setting `document.body.style.overflow`, while the `Drawer` it wraps composes
  `useOverlayBehavior`, which already owns scroll lock
- **Issue:** Two independent owners of `body.overflow`. On cleanup, the nav bar sets it
  to `""` unconditionally — if any other overlay is open at that moment, it silently
  unlocks the page beneath it.
- **User impact:** Rare, but when it hits, the background scrolls under an open modal —
  the classic "why is the page moving behind the dialog" bug.
- **Fix:** Delete the effect. `Drawer` already handles it.

---

### [Severity 1] Theme-blind colors in the top bar

- **Principle:** 4 — Consistency and Standards
- **Location:** `top-bar.tsx:135` (`BrandMark className="text-white"` on `bg-primary`),
  `:215` (`bg-slate-800 ... text-white` avatar)
- **Issue:** `DESIGN.md`: pair a background with its `-foreground`; hardcoding
  `text-white` on `bg-primary` breaks in dark mode, where primary becomes orange. The
  avatar's `bg-slate-800` ignores the token system entirely.
- **Fix:** `text-primary-foreground` on the brand mark; `bg-muted text-muted-foreground`
  (or `bg-primary`/`text-primary-foreground`) on the avatar.

### [Severity 1] Collection cards hover orange in both themes

- **Principle:** 4 — Consistency and Standards
- **Location:** `frontend/src/components/model-grid.tsx:861` —
  `hover:border-orange-500 dark:hover:border-orange-500`
- **Issue:** Sitting in the same grid as `ModelCard`, whose hover is `hover:border-primary`
  (blue in light, orange in dark). So in light mode the folder cards hover orange and the
  model cards hover blue, side by side.
- **Fix:** `hover:border-primary`.

### [Severity 1] Card titles are `<h4>` under an `<h1>`

- **Principle:** 13 — Accessibility (heading hierarchy)
- **Location:** `frontend/src/components/model-card.tsx:210`
- **Issue:** The grid's `<h1>` is the collection name; each card's title is an `<h4>`,
  skipping h2 and h3. Screen-reader users navigating by heading get a broken outline.
- **Fix:** `<h2>` (or `<h3>` if the tab bar is meant to be a level).

### [Severity 1] The 404 page ignores the page system

- **Principle:** 4 — Consistency and Standards
- **Location:** `frontend/src/pages/not-found.tsx`
- **Issue:** Hand-rolled centering, a `<p>` where the `<h1>` should be, a hand-styled
  button instead of `Button`, and `on-surface` tokens where every other page uses
  `foreground`. `DESIGN.md` is explicit that a document page composes `PageContainer` +
  `PageHeader`.
- **Fix:** Compose `PageContainer` + `PageHeader` + `Button asChild`, and make "404" the
  actual `<h1>`.

---

## Strengths

Genuinely good work here, and it should be left alone:

- **The overlay primitives are exemplary** (`ui/modal.tsx`, `ui/drawer.tsx`,
  `ui/dropdown-menu.tsx`, `lib/overlay.ts`). Portal, focus trap, focus restore on close,
  Escape, scroll lock, outside-pointerdown dismiss, origin-aware entrance, and — the part
  nearly everyone skips — a *symmetric exit* animation via `useMountTransition`. Most
  codebases have at least one hand-rolled modal missing four of those. This one has none.
  (H3, H13)
- **Motion respects the user.** `transition-all` count is genuinely zero across
  `frontend/src`, no raw `duration-*` values exist outside the token scale, and the
  `prefers-reduced-motion` block correctly matches the `:nth-child` specificity of the
  stagger rules so the override actually wins. That last detail is a trap almost everyone
  falls into. (H7, H14)
- **Loading and async feedback are thorough.** Skeletons on first load, `keepPreviousData`
  so filter changes don't flash, a distinct "Updating…" hint for background refetches, a
  task center with real per-step progress from the backend, and thumbnails that fade in on
  load (including the cached-image `node.complete` edge case). (H1)
- **Destructive actions are all confirmed** and the copy tells the truth about
  consequences — "They move to the trash and can be restored until purged"
  (`batch-toolbar.tsx:127`) is exactly the right sentence. Trash + purge means real undo,
  not just a scary dialog. (H3, H15)
- **The error-message catalogue is unusually good.** `lib/errors.ts` maps ~40 server codes
  to plain-language, *actionable* copy — "MakerWorld requires you to be logged in to
  download this model. Connect MakerWorld under Settings → Imports and try again." That's
  a fix instruction, not an error. It makes the `"unknown"` fallback above all the more
  worth closing. (H9)
- **Keyboard support in the composite widgets is real**: `TabBar` has roving tabindex and
  arrow keys, `useComboboxNav` gives the tag pickers listbox ARIA and arrow navigation, and
  `DropdownMenu` refocuses its trigger on Escape. (H7, H13)

## Suggested order of work

1. The five Severity 3s — they're all small, and three of them (`unknown` toast, disabled
   button styling, dark-mode error banner) are under ten lines each.
2. The token/consistency sweep — hardcoded reds, `text-[9px]`, focus rings, `text-white`,
   the orange hover. One pass, mechanical, and it takes `DESIGN.md`'s zero-counts back to
   zero.
3. The affordance fixes — the `/` shortcut, the fake "⋮", `aria-current`, `document.title`.
4. Mobile batch selection and the filtered empty state — the two findings that change what
   users can actually get done.

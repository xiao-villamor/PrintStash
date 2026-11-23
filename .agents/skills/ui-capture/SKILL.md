---
name: ui-capture
description: Capture and audit PrintStash UI in a real browser so visual bugs are caught before the user sees them — frame-by-frame motion (pane toggle, card reflow, skeleton → content, folder navigation) and rest invariants (clipped cards, grids wider than their box, pop-ins, media flashes). Use after every UI or motion edit, before reporting a UI change, and whenever the user sends a screenshot of a visual bug.
---

# UI capture and motion audit

A screenshot taken after a fixed `waitForTimeout` proves almost nothing: it
misses everything that happens between frames (teleports, pop-ins, a second
reflow at rest, thumbnails that blank and refill) and it is often taken from a
stale page. Use the audit tool; read its findings; then look at the frames.

## The tool

```bash
cd frontend
PRINTSTASH_USER=… PRINTSTASH_PASSWORD=… node scripts/motion-audit/motion-audit.mjs \
  --base http://localhost:3470 --out ../.motion-audit/<label> \
  --scenario rest,sidebar-close,sidebar-open,load,folder \
  --path "/?type=all&sort=date-desc&c=<collection path>&cid=<id>" \
  [--viewport 1920x1080] [--latency 700] [--scheme dark]
```

Each scenario performs one real gesture in a fresh page and samples **every
animation frame**: geometry, effective opacity (own × ancestors), horizontal
clip and thumbnail visibility of every card (`[data-library-key]`), the pane,
headings, disclosures and status regions. It also records long animation
frames, layout shifts and a CDP screencast.

Output: `<out>/report.md` (all findings), per scenario `report.json` (full
trace), `filmstrip.png` and `frames/NNN_<ms>.jpg`. Exit 1 when anything is
found. `.motion-audit/` is gitignored.

| Scenario | Gesture |
| --- | --- |
| `rest` | none — the page at rest, for the rest invariants |
| `sidebar-close` / `sidebar-open` | "Toggle sidebar" in the top bar |
| `load` | cold reload with `/models/browse` delayed by `--latency` (skeleton → cards) |
| `folder` | click the first collection card, browse delayed (navigation + skeleton) |

Navigation cost per folder change (requests by endpoint, exact duplicates,
full reloads, time until cards show, long frames), cold cache:

```bash
PRINTSTASH_USER=… PRINTSTASH_PASSWORD=… node scripts/motion-audit/nav-trace.mjs \
  --base http://localhost:3474 --path "/?c=<folder>&cid=<id>" --child <child-slug> --parent <Parent name>
```

Virtualization (TanStack Virtual) only engages above 120 loaded items; check it
on a library that large, not on a small real one.

## The Playwright MCP window is not a reliable input source

Its injected hit-target interceptor can stop delivering real pointer and
keyboard input (no `pointerdown` reaches `document`, nothing navigates, no
error). Before calling anything "a click that does nothing" a bug: record
`pointerdown` on `document`; if it never fires, it is the harness. Call the
element's React `onClick` to confirm the app path, then run the gesture in the
headless scripts above. Detach every CDP session a script opens
(`await cdp.detach()`), and close/reopen the MCP browser when input stops.
Use the MCP window to look; use the scripts to measure and to click.

## Findings and what they mean

| Finding | Defect |
| --- | --- |
| `clipped-at-rest` | a card is cut by its row's edge after everything settled |
| `grid-overflow` | a grid holds more columns than its width (outside pane motion) |
| `teleport` | an element jumps >12 px between two still frames |
| `pop-in` / `pop-out` | appears or vanishes at ≥90 % opacity with no fade or clip reveal |
| `opacity-snap` | opacity changes >0.45 in one frame |
| `media-flash` / `media-blank` | a visible thumbnail disappears / a remounted card shows no thumbnail |
| `skeleton-mismatch` / `skeleton-orphan` | the content does not take the skeleton's boxes |
| `sequential-motion` | motion in separate bursts (e.g. a reflow that waits for rest) |
| `dropped-frames` / `long-frame` | jank; `long-frame` names the script responsible |
| `layout-shift` | CLS from the gesture |

## Procedure (mandatory for UI work)

1. **Baseline first.** Run the scenarios that touch the change *before*
   editing, into `.motion-audit/before`. Keep it for the comparison table.
2. **A user screenshot of a bug is a red test.** Reproduce it at the user's
   URL and at the screenshot's width (`--viewport <w>x<h>`) and get a finding
   for it before fixing. If no finding fires, the detector is incomplete:
   extend `motion-audit.mjs` until it fires, then fix the UI. Never claim a fix
   the tool could not have caught.
3. **Fresh pages only.** HMR keeps component state (geometry, caches) from
   earlier code; the tool opens a new page per scenario. When looking manually,
   reload after every edit. Never swap built files under a running server.
4. **Wait for a condition, not a timeout.** Settle on data (cards outside the
   placeholder, no `/models/browse` in flight) and then record a tail; the
   tool does this. A bug that appears 500 ms after "ready" is still a bug.
5. **Read the frames at each finding's timestamp.** Open the `frames/` JPGs
   closest to the finding, plus the first and last frame. Screencast
   timestamps are approximate; the trace (`report.json`) is per frame and
   authoritative.
6. **Timing on a production build.** Dev React inflates long frames 3–5×.
   Judge visuals on the dev server; judge `long-frame`/`dropped-frames` on
   `vite build` + `vite preview` with the same backend. Publish it on the
   URL the user already uses (the session lives in per-origin storage, so a
   new port shows them the sign-in page), confirm that URL serves the new
   `assets/index-*.js`, and check it signed in before reporting.
7. **Report with numbers.** Before/after table per scenario: findings by kind,
   motion settled ms, p95 frame ms. List what remains, by finding.

## Iterate on the design in Playwright

For a redesign, pair the motion audit with a walkthrough on the preview the user
will open. A page can pass geometry checks while its next action is hard to
find. Keep captures and observations in the task's private `reports/` folder.

1. Start from the user's screenshot or ordinary entry route. Write down the
   operator's task and what on screen should tell them their next step. Capture
   both the first visit and a representative populated state.
2. Compare the working page with the prototype at the same viewport, sidebar
   state, theme and zoom. Inspect the screenshot at full size. Look for space
   a repeated heading or oversized panel takes away from useful information.
   For device views, check an idle machine as well as a running one.
   Align the content origins before comparing captures. Measure the first
   actionable row and the top of each important panel. A matching column
   count can still put the next control below the fold. Compare sparse and
   populated lists so an explorer works with both one file and many folders.
3. Walk the task with real pointer and keyboard input. Try the *expected*
   targets, including a card's image and body, then try its secondary controls
   independently. Observe focus, hover, pending feedback and reduced motion
   where relevant. For a write, check the visible receipt and persisted value
   after reload on the designated test instance.
   For a file editor, select a disposable file by its full path and check the
   request targets that file. Reopen the URL after reload and in a second tab
   to verify selection, unsaved drafts and concurrent saves.
   Include a file longer than the visible editor. Compare the source and
   gutter heights and line spacing at the top, midway down, and on the last
   line. Scroll horizontally and switch to a short file. A saved value alone
   cannot tell you whether an operator can read it.
   On a live control screen, perform one adjustment while watching the process
   it affects. Check that the command, its acknowledgement and the measured
   response are visible on the same screen. A heater target and the measured
   temperature answer different operator questions.
4. Record each actionable mismatch as a state, gesture, observed result and
   expected result. Save the frame or measurable assertion that would have
   caught it. Where several arrangements could work, compare a second
   composition against the same operator task before choosing one.
5. Change the design, open a fresh page and replay the original gesture plus
   the next step in the workflow. Keep the before/after pair. Repeat while a
   reproduced issue still affects the task. Report the states exercised and
   any remaining mismatch with its evidence.

## Rest checks that must always pass

At 1920×1080 (the user's real resolution) on the views below, `rest` has zero
`clipped-at-rest` and zero `grid-overflow`:

- mixed: `/?c=printstash-data%2Fprintstash-data&type=all&sort=date-desc&cid=2`
- folder with collections: `/?type=all&sort=date-desc&c=printstash-data%2Fprintstash-data%2Forganization&cid=8`
- leaf: `/?c=printstash-data%2Fprintstash-data%2Forganization%2Fgridfinity%2Fmodern-case%2F4x4x6&type=all&sort=date-desc&cid=15`
- many models, one row: `/?type=all&sort=date-desc&c=printstash-data%2Fprintstash-data%2Frandom%2Frocky&cid=25`

## Extending the tool

Add a scenario to `scenarios` in `motion-audit.mjs` (it returns the gesture,
or `{ reload: true, gesture }` for a navigation). Add an element to `TRACKED`
to audit it. Add a finding in `analyse()`; prove it with a deliberately broken
build (it must fire) and the fixed build (it must not).

# 006 — Kill `transition-all`, fix progress bars, add press feedback to cards

- **Status**: DONE
- **Commit**: 57770ff
- **Depends on**: 001 (`duration-press`/`duration-slow` utilities). Run AFTER 002-005 — several target lines also get color-token edits from 002; match on the `transition-*` fragment, not the whole line.
- **Severity**: HIGH
- **Category**: Performance / Physicality
- **Estimated scope**: 11 files, ~25 single-line edits + 3 small rewrites

## Problem

1. **`transition-all` at 22 sites** animates unintended properties off-GPU — always a finding. Worst cases are the model cards (rendered dozens of times, high-frequency surface).
2. **Three progress bars animate `width`** (layout + paint every frame) via `transition-all`, two of them at 500ms with a non-linear curve — constant motion should be `linear`, and progress fills should composite on the GPU:

```tsx
/* frontend/src/components/top-bar.tsx:345-349 — current */
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-muted">
            <div
              className={`h-full transition-all duration-300 ${task.status === "failed" ? "bg-red-500" : "bg-blue-600 dark:bg-orange-600"}`}
              style={{ width: `${task.progress}%` }}
            />
          </div>
```

3. **The most-clicked elements have no press feedback** (model cards), and several rows use `active:scale-95` **without transform in their transition list** so the press snaps:

```tsx
/* frontend/src/components/bottom-nav-bar.tsx:218 — current */
const className = `flex flex-col items-center justify-center gap-2 rounded-xl border p-4 text-center transition-colors active:scale-95 ${
```

## Target

Every transition names its properties; progress bars are `transform: scaleX()` with `linear` timing; every `active:scale` has transform in its transition list; scale amounts sit in the subtle 0.95–0.99 band appropriate to element size.

## Repo conventions to follow

- `duration-press` (150ms), `duration-fast` (200ms), `duration-slow` (300ms) and the strong `ease-out` exist after plan 001.
- Correct existing exemplar: `frontend/src/components/fab.tsx:9` — `active:scale-95 transition-transform duration-150`.

## Steps

### Step 1 — Progress bars → GPU `scaleX`

**`frontend/src/components/top-bar.tsx:345-349`** — replace the inner div with:

```tsx
            <div
              className={`h-full w-full origin-left transition-transform duration-slow ease-linear ${task.status === "failed" ? "bg-red-500" : "bg-primary"}`}
              style={{ transform: `scaleX(${Math.min(100, task.progress) / 100})` }}
            />
```

**`frontend/src/components/printer-detail.tsx:508-512`** — replace the inner div with:

```tsx
                <div
                  className="h-full w-full origin-left bg-primary transition-transform duration-slow ease-linear"
                  style={{ transform: `scaleX(${Math.min(100, progress ?? 0) / 100})` }}
                />
```

**`frontend/src/components/printer-detail.tsx:1313-1317`** — replace the inner div with:

```tsx
          <div
            className="h-full w-full origin-left bg-primary transition-transform duration-slow ease-linear"
            style={{ transform: `scaleX(${pct / 100})` }}
          />
```

(The outer `overflow-hidden rounded` containers stay and keep the rounded clip. `bg-primary` assumes plan 002 ran; if the blue/orange classes are still present, keep them. 500ms → 300ms brings the fills inside the UI duration budget; `ease-linear` because progress is constant motion.)

### Step 2 — Fragment replacements (match the FROM fragment inside the cited line)

| # | File:line | FROM fragment | TO fragment |
| --- | --- | --- | --- |
| 1 | `model-card.tsx:144` | `transition-all duration-200` | `transition-[color,background-color,border-color,box-shadow,opacity,transform] duration-200 active:scale-[0.99]` |
| 2 | `model-grid.tsx:854` | `hover:shadow-sm transition-all` | `hover:shadow-sm transition-[border-color,box-shadow,transform] duration-fast active:scale-[0.99]` |
| 3 | `model-grid.tsx:571` | `transition-all` | `transition-colors` |
| 4 | `model-grid.tsx:579` | `transition-all` | `transition-colors` |
| 5 | `model-grid.tsx:587` | `transition-all` | `transition-colors` |
| 6 | `model-grid.tsx:598` | `transition-all` | `transition-colors` |
| 7 | `model-grid.tsx:612` | `transition-all` | `transition-[color,background-color,box-shadow]` |
| 8 | `model-grid.tsx:619` | `transition-all` | `transition-[color,background-color,box-shadow]` |
| 9 | `sidebar-nav.tsx:48` | `transition-all active:scale-95` | `transition-[color,background-color,transform] duration-press active:scale-[0.98]` |
| 10 | `sidebar-nav.tsx:96` | `transition-all active:scale-95` | `transition-[color,background-color,transform] duration-press active:scale-[0.98]` |
| 11 | `sidebar-nav.tsx:113` | `transition-all active:scale-95` | `transition-[color,background-color,transform] duration-press active:scale-[0.98]` |
| 12 | `mobile-nav-drawer.tsx:84` | `transition-all active:scale-95` | `transition-[color,background-color,transform] duration-press active:scale-[0.98]` |
| 13 | `mobile-nav-drawer.tsx:126` | `transition-all active:scale-95` | `transition-[color,background-color,transform] duration-press active:scale-[0.98]` |
| 14 | `mobile-nav-drawer.tsx:146` | `transition-all active:scale-95` | `transition-[color,background-color,transform] duration-press active:scale-[0.98]` |
| 15 | `theme-toggle.tsx:49` | `transition-all` | `transition-colors` |
| 16 | `filament-profiles-card.tsx:533` | `transition-all` | `transition-[opacity,color,background-color,border-color]` |
| 17 | `filament-profiles-card.tsx:697` | `transition-all` | `transition-[opacity,color,background-color,border-color]` |
| 18 | `printers-list.tsx:121` | `transition-all duration-200` | `transition-[box-shadow,border-color] duration-200` |
| 19 | `model-detail/files-tab.tsx:27` | `transition-all` | `transition-[border-color,box-shadow]` |
| 20 | `bottom-nav-bar.tsx:218` | `transition-colors active:scale-95` | `transition-[color,background-color,border-color,transform] duration-press active:scale-[0.98]` |

All paths relative to `frontend/src/components/`. Sites 1-2 add press feedback to the grid cards — `0.99` (not 0.97) because these are large surfaces where stronger compression looks cartoonish; the list-mode row (`model-grid.tsx:940`, `active:bg-muted`) already has adequate feedback and is not touched. Sites 9-14 also soften `scale-95` → `scale-[0.98]` on full-width nav rows.

Line numbers are as of commit 57770ff and may have shifted after plans 002-005; locate each by the FROM fragment (`grep -n "transition-all" frontend/src -r --include="*.tsx"` should list exactly sites 1-19 pre-edit). If a FROM fragment doesn't exist in the named file, STOP and report that site.

### Step 3 — Confirm zero stragglers

```bash
grep -rn "transition-all" frontend/src --include="*.tsx" --include="*.css"   # → 0 hits
```

If new `transition-all` uses appeared from plans 003-005 drift, apply the same property-list treatment and note them in your report.

## Boundaries

- Do NOT touch hover color choices, layout classes, or handlers — only transition/active fragments and the three progress-bar inner divs.
- Do NOT add press feedback to plain text links or the list-mode rows.
- Do NOT change `fab.tsx` or `bottom-nav-bar.tsx:127` (`NavTab`) — they are already correct.
- If a line's surrounding code differs materially from the excerpts (drift), STOP on that site and report.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` && `pnpm --dir frontend lint` && `pnpm --dir frontend test` pass; the Step 3 grep returns nothing.
- **Feel check**:
  - Start an upload and watch the task popover progress bar: it fills smoothly at constant speed with no easing "lurch"; in DevTools Performance, the bar animation shows no layout (purple) work per frame.
  - Press-and-hold a model card: it compresses very slightly (99%) and smoothly; releasing springs back. Spam-click: no snapping (transitions retarget).
  - Hover cards/nav rows: color/shadow feedback unchanged from before.
- **Done when**: zero `transition-all` in `frontend/src`, all three progress bars composite-only, and every `active:scale` element transitions its transform.

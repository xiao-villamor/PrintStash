# 002 — Unify the color/token system and retire hand-encoded theme colors

- **Status**: PARTIAL — see README

> Deviations from the literal plan (all checks green; changes are additive completions, no steps skipped):
> - Added `"on-secondary-container": "var(--on-secondary-container)"` to the Tailwind config color map (following the plan's own `on-error-container`/`on-tertiary-container` pattern in Step 2). The Step 3 codemod turns `text-[var(--on-secondary-container)]` into `text-on-secondary-container`, which the plan's mapping (`secondary-container.foreground` → class `secondary-container-foreground`) does not resolve; the added top-level mapping makes it resolve.
> - Converted three fallback-form arbitrary classes the Step 3 regex intentionally skipped — `text-[var(--destructive,#dc2626)]` and `hover:text-[var(--destructive,#dc2626)]` in `external-libraries-panel.tsx` — to `text-destructive` / `hover:text-destructive`, to satisfy the Step 6 `[var(--` == 0 requirement (`destructive` is config-mapped).
> - Straggler sweep (Step 4 final paragraph, discretionary): converted the clearly primary-hue paired patterns; deliberately kept three non-primary leftovers — `model-grid.tsx:857` (orange-in-both drop-zone hover, not a light/dark pair), `gcode-viewer.tsx:442` and `model-detail/index.tsx:423` (blue-in-both-theme decorative badges/tabs). Also converted the two statistics.tsx UI-chrome hits (segmented toggle + KPI icon chip; the chart series palette was left untouched per boundary).
- **Commit**: 57770ff
- **Severity**: HIGH
- **Category**: Design system / tokens
- **Estimated scope**: ~35 files, mostly mechanical codemods driven by exact string replacement

## Problem

Two overlapping color systems describe the same surfaces, and the theme's primary color is manually re-encoded at dozens of call sites:

1. **The Material-3 surface tokens are only ever used via arbitrary classes.** `frontend/tailwind.config.cjs:52-78` maps `surface`, `surface-container-*`, `on-surface`, `outline`, etc. as Tailwind colors, but zero code uses the mapped form (`bg-surface-container-low`). Instead there are ~837 arbitrary usages like `text-[var(--on-surface-variant)]` across 32 files. `tailwind-merge` cannot dedupe arbitrary `[var()]` classes, and the config block is dead weight.

2. **Primary is hand-encoded as `blue-* dark:orange-*` pairs**, defeating `--primary`:

```tsx
/* frontend/src/components/ui/checkbox.tsx:31 — current */
? "border-blue-600 bg-blue-600 text-white dark:border-orange-600 dark:bg-orange-600"
: "border-border bg-background/80 text-transparent hover:border-blue-500 dark:hover:border-orange-500",
```

3. **Two latent bugs**: `frontend/src/components/fab.tsx:9` uses `text-[var(--on-primary)]` and `frontend/src/components/model-detail/index.tsx:494` uses `var(--surface-variant)` — **neither variable exists** in `globals.css`, so both resolve to nothing.

4. **The radius scale is degenerate**: `frontend/tailwind.config.cjs:84-87` defines `md: calc(var(--radius) - 0px)` (identical to `lg`), while the dominant radius in the app is bare `rounded` (413 uses, Tailwind default 0.25rem) — off-scale, matching no token.

5. **Missing token layers**: no status colors (success/warning are hardcoded emerald/amber), no z-index scale (dropdowns and dialogs both fight in `z-50` while `ui/modal.tsx` uses `z-[100]`), and an off-scale type ramp (`text-[11px]` ×103, `text-[10px]` ×164).

6. **Focus rings are themed 5 different ways**, including hardcoded `focus:ring-orange-500` (wrong in light theme) and `focus:ring-blue-600 dark:focus:ring-orange-500` where `focus:ring-ring` exists for exactly this.

## Target

- All Tailwind color classes reference config-mapped tokens; **zero `[var(--…)]` arbitrary color classes remain** in `.tsx` files.
- All `blue-* dark:orange-*` primary encodings become `primary`/`accent`/`ring` token classes.
- New tokens: `--primary-hover`, `--primary-soft` (40% alpha for selection rings/borders), `--success`/`--warning` (+foregrounds), z-index scale, `text-2xs`/`text-3xs` type steps.
- `rounded` (bare) resolves to `var(--radius)`; `rounded-md` is a real step below `lg`.

## Repo conventions to follow

- Color tokens are hex CSS custom properties defined in the `:root` and `.dark` blocks of `frontend/src/globals.css` and mapped in `frontend/tailwind.config.cjs` `theme.extend.colors` as plain `var(...)` strings (see `tailwind.config.cjs:19-78`).
- Do NOT convert tokens to RGB-triplet/`<alpha-value>` form — `frontend/src/components/toaster.tsx:14-16` and `frontend/src/globals.css:222-233` consume the vars as complete colors. Alpha needs are covered by the explicit `--primary-soft` token instead.

## Steps

### Step 1 — Add new tokens to `frontend/src/globals.css`

In the `:root` block, immediately after `--radius: 0.375rem;` (line 32), add:

```css
    /* Interaction shades of primary (hover = one step darker; soft = 40% for
       selection rings and tinted borders). */
    --primary-hover: #1d4ed8;
    --primary-soft: rgb(37 99 235 / 0.4);

    /* Status */
    --success: #16a34a;
    --success-foreground: #ffffff;
    --warning: #d97706;
    --warning-foreground: #ffffff;
```

In the `.dark` block, immediately after `--ring: #fb923c;` (line 99), add:

```css
    --primary-hover: #ea580c;
    --primary-soft: rgb(251 146 60 / 0.4);

    --success: #4ade80;
    --success-foreground: #052e16;
    --warning: #fbbf24;
    --warning-foreground: #451a03;
```

### Step 2 — Extend `frontend/tailwind.config.cjs`

Inside `theme.extend.colors`, add (as siblings of the existing entries):

```js
        "primary-hover": "var(--primary-hover)",
        "primary-soft": "var(--primary-soft)",
        success: {
          DEFAULT: "var(--success)",
          foreground: "var(--success-foreground)",
        },
        warning: {
          DEFAULT: "var(--warning)",
          foreground: "var(--warning-foreground)",
        },
        // Mappings that existed as CSS vars but were never wired into Tailwind:
        error: "var(--error)",
        "on-error-container": "var(--on-error-container)",
        "on-tertiary-container": "var(--on-tertiary-container)",
        sidebar: "var(--sidebar-bg)",
```

Replace the `borderRadius` block (`tailwind.config.cjs:84-88`) with:

```js
      borderRadius: {
        DEFAULT: "var(--radius)",
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 0.125rem)",
      },
```

Add as siblings of `borderRadius` inside `theme.extend`:

```js
      fontSize: {
        // The app's mono-label ramp, previously text-[11px]/text-[10px].
        "2xs": "0.6875rem",
        "3xs": "0.625rem",
      },
      zIndex: {
        dropdown: "50",
        overlay: "100",
      },
```

### Step 3 — Codemod: M3 arbitrary var classes → mapped classes

Run from `frontend/` (macOS BSD sed). This rewrites every `<utility>-[var(--x)]` to `<utility>-x`, which is valid for every var that appears in the config's color map (all of them after Step 2):

```bash
cd frontend
LC_ALL=C find src -name "*.tsx" -exec sed -i '' -E \
  's/(bg|text|border|ring|fill|stroke|decoration|divide|outline|accent|caret|shadow)-\[var\(--([a-z0-9-]+)\)\]/\1-\2/g' {} +
```

Then fix the two cases the regex produces wrong/for-nonexistent vars:

- `frontend/src/components/fab.tsx:9`: the sed yields `text-on-primary` — **`--on-primary` does not exist**. Replace `text-on-primary` with `text-primary-foreground`.
- Any produced `*-sidebar-bg` → rename to `*-sidebar` (the config maps `sidebar`); check with `grep -rn "sidebar-bg\b" src --include="*.tsx"` — expected in `app-shell.tsx` or none.
- `frontend/src/components/model-detail/index.tsx:494`: `style={{ boxShadow: "inset 0 0 0 1px var(--surface-variant)" }}` — **`--surface-variant` does not exist**. Replace with `var(--outline-variant)`.

Verify: `grep -rn "\[var(--" src --include="*.tsx"` must return **zero** hits. If any remain (a var not in the config map), STOP and report them instead of inventing mappings.

### Step 4 — Codemod: hand-encoded primary → tokens

Apply these exact replacements repo-wide (`find src -name "*.tsx" -exec sed -i '' 's|FROM|TO|g' {} +` per row; the strings contain `/`, so use `|` as the sed delimiter):

| FROM (exact string) | TO |
| --- | --- |
| `bg-blue-600 dark:bg-orange-600` | `bg-primary` |
| `bg-blue-500 dark:bg-orange-500` | `bg-primary` |
| `hover:bg-blue-700 dark:hover:bg-orange-700` | `hover:bg-primary-hover` |
| `text-blue-600 dark:text-orange-500` | `text-primary` |
| `text-blue-600 dark:text-orange-400` | `text-primary` |
| `text-blue-700 dark:text-orange-400` | `text-accent-foreground` |
| `bg-blue-50 dark:bg-orange-500/10` | `bg-accent` |
| `border-blue-600 dark:border-orange-600` | `border-primary` |
| `hover:border-blue-500 dark:hover:border-orange-500` | `hover:border-primary` |
| `hover:border-blue-600 dark:hover:border-orange-500` | `hover:border-primary` |
| `border-blue-500 dark:border-orange-500` | `border-primary` |
| `ring-blue-600/40 dark:ring-orange-600/40` | `ring-primary-soft` |
| `ring-blue-500/40 dark:ring-orange-500/40` | `ring-primary-soft` |
| `border-blue-600/40 dark:border-orange-500/40` | `border-primary-soft` |
| `focus:ring-blue-600 dark:focus:ring-orange-500` | `focus:ring-ring` |
| `focus:ring-orange-500` | `focus:ring-ring` |
| `hover:text-blue-600 dark:text-orange-500` | `hover:text-primary dark:text-orange-500` → see note A |
| `bg-blue-50 text-blue-700 dark:bg-orange-950/40 dark:text-orange-400` | `bg-accent text-accent-foreground` |

**Note A** — `frontend/src/components/theme-toggle.tsx:49` reads `text-muted-foreground hover:text-blue-600 dark:text-orange-500` — the dark variant is an always-on color, not a hover. Replace the whole fragment `hover:text-blue-600 dark:text-orange-500` with `hover:text-primary` (dark resting color becomes muted-foreground like light mode — this is a deliberate consistency fix).

Two adjacent-word cleanups after the table runs:

- Where `text-white` sits next to a now-`bg-primary` (e.g. `frontend/src/components/model-grid.tsx:587`, `frontend/src/components/printers-list.tsx:110`, `frontend/src/components/batch-toolbar.tsx:265`, `frontend/src/components/top-bar.tsx:197`, checkbox below): replace that `text-white` with `text-primary-foreground`. Find them: `grep -rn "text-white" src --include="*.tsx" | grep "bg-primary"`. **Intended visual change**: in dark mode, text on primary buttons becomes near-black (`#0d1117`) on orange instead of white on orange — this matches `--primary-foreground` and is the correct contrast pairing.
- `frontend/src/components/ui/checkbox.tsx:29-32` becomes:

```tsx
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-background/80 text-transparent hover:border-primary",
```

Sweep the stragglers: `grep -rnE "(text|bg|border|ring)-(blue|orange)-[0-9]+" src --include="*.tsx"` — for each remaining hit, if it encodes the primary hue (paired light/dark or used for active/selected state), map it with the same logic as the table (`bg-*`→`bg-primary`, active-tint→`bg-accent`, etc.); if it is genuinely non-primary decoration (e.g. chart palettes in `statistics.tsx`, 3D-viewer colors in `stl-viewer.tsx`/`gcode-viewer.tsx`), leave it. Two known primary-hue cases to convert manually because they mix in extra classes: `frontend/src/components/printer-detail.tsx:1132` (`border-blue-600 … dark:border-orange-500` tab underline — becomes `border-primary`) and `frontend/src/components/top-bar.tsx:177` (`bg-blue-500 dark:bg-orange-500` bell dot — covered by row 2).

### Step 5 — Codemod: off-scale type ramp

```bash
LC_ALL=C find src -name "*.tsx" -exec sed -i '' 's/text-\[11px\]/text-2xs/g; s/text-\[10px\]/text-3xs/g' {} +
```

Leave `text-[9px]` and `text-[13px]` (rare) untouched.

### Step 6 — Verification greps

All must hold before finishing:

```bash
grep -rn "\[var(--" src --include="*.tsx" | wc -l      # 0
grep -rn "text-\[11px\]\|text-\[10px\]" src | wc -l    # 0
grep -rnE "dark:(bg|text|border|ring)-orange" src --include="*.tsx"  # only non-primary leftovers you deliberately kept
grep -rn "on-primary)" src                              # 0
grep -rn "surface-variant" src/components/model-detail/index.tsx  # only outline-variant
```

## Boundaries

- Do NOT touch `.css` values of existing tokens (the palette itself does not change, except the two new token groups).
- Do NOT convert status reds/greens/ambers (`text-red-600`, `text-emerald-600`, `bg-amber-*`, chip palettes) — only primary-hue encodings. Status-color adoption happens via Badge variants in plan 005.
- Do NOT change chart colors in `statistics.tsx` or WebGL colors in `stl-viewer.tsx`/`gcode-viewer.tsx`.
- Do NOT rename CSS variables or delete the M3 var definitions from `globals.css`.
- Do NOT add new dependencies.
- If a sed produces a class Tailwind can't resolve (check via the build), STOP and report.

## Verification

- **Mechanical**: `pnpm --dir frontend typecheck` && `pnpm --dir frontend lint` && `pnpm --dir frontend build` all pass. The Step 6 greps hold. `pnpm --dir frontend test` passes (there are component tests under `src/components/__tests__/` that may assert class strings — if one fails on an intentionally-changed class, update the assertion and say so in your report).
- **Feel check**: run the app in both themes.
  - Light: primary buttons still blue; dark: primary buttons orange with **dark** text (intended change).
  - Model card selected state still shows a visible soft ring in both themes.
  - Keyboard-focus an input in Settings: ring is blue (light) / orange (dark) — never orange-in-light.
  - Corner radii look unchanged-or-slightly-softer everywhere (bare `rounded` grew 0.25→0.375rem).
- **Done when**: zero arbitrary `[var(--…)]` color classes and zero primary-hue `blue/orange` pairs remain, and the app renders identically in spirit (same palette, tokens underneath).

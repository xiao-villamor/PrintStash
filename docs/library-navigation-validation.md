# Library navigation implementation validation

Base: `710e4eb7aafe05642693d6ff2696146e042dca53`, including startup changes.
Original dirty checkout preserved. M2/M5 slice; full migration is not complete.

Before edits: model-grid/filter-sidebar baseline **203 passed / 2 files**.
Retired-mode regression selection: **4 failed / 4 passed** before production changes.
Document history regression failed before removing its local state mirror.
Saved-mode regressions: **2 failed** before adding the saved filter field.
After changes: **238 passed / 7 files** including URL, grid, sidebar, saved-view
selector, search controls/filter codec and saved-view transport; app/UI/domain
TypeScript checks passed. Browser: **21 passed** in `tests/e2e/vault.spec.ts`; lint zero diagnostics;
format:check passed on 664 files; diff whitespace check passed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | normalizes retired URL mode %s | Edge | organized/components with folder, tags and sort | canonical Everything retains context | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::normalizes retired URL mode %s` |
| 2 | normalizes retired preference %s | Edge | organized/components preference | Everything captured in URL | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::normalizes retired preference %s` |
| 3 | gives an explicit mode priority over preferences | Happy | URL multipart, stored all | multipart selected | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::gives an explicit mode priority over preferences` |
| 4 | initializes absent inputs from valid preferences | Happy | valid stored mode/sort | canonical URL captures both | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::initializes absent inputs from valid preferences` |
| 5 | rejects malformed explicit values instead of using preferences | Error | invalid mode/sort | all/date-desc canonical values | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::rejects malformed explicit values instead of using preferences` |
| 6 | preserves document navigation parameters | Edge | v=docs with folder/filter | docs location remains intact | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::preserves document navigation parameters` |
| 7 | maps the legacy multipart bookmark | Edge | v=multipart | canonical type=multipart | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::maps the legacy multipart bookmark` |
| 8 | gives the explicit mode priority over a legacy bookmark | Edge | v=multipart and type=all | explicit all wins | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::gives the explicit mode priority over a legacy bookmark` |
| 9 | is stable when its canonical URL is read again | Edge | canonical output as input | same location returned | Frontend unit | ✅ `src/features/library/__tests__/url.test.ts::readLibraryLocation::is stable when its canonical URL is read again` |
| 10 | keeps referenced Models visible after retiring Organized | Happy | referenced Model and retired preference | both Model and set visible | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::keeps referenced Models visible after retiring Organized` |
| 11 | exposes only the supported library view controls | Happy | sidebar rendered | only Everything/Multipart Sets offered | Frontend unit | ✅ `src/components/__tests__/filter-sidebar.test.tsx::FilterSidebar::exposes only the supported library view controls` |
| 12 | restores URL-owned mode on history navigation | Edge | multipart location then Back | Everything restored from history | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::restores URL-owned mode on history navigation` |
| 13 | writes a sort choice into the URL | Happy | select Name A–Z | URL includes name-asc | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::writes a sort choice into the URL` |
| 14 | avoids the retired global grouping request | Edge | global grouping unavailable | Model remains accessible without grouping error | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::avoids the retired global grouping request` |
| 15 | follows the document tab through browser history | Edge | documents location, Back, Forward | selected tab follows each entry | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::follows the document tab through browser history` |
| 16 | saves the library mode | Happy | save Multipart Sets view | request includes library_view=multipart | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::saves the library mode` |
| 17 | restores the saved library mode | Happy | choose Multipart Sets saved view | multipart becomes active | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ModelBrowser::restores the saved library mode` |
| 18 | restores library mode after collection history | Edge | mode changed in child folder then Back | previous mode/sort restored despite changed preference | Playwright | ✅ `tests/e2e/vault.spec.ts::vault route::restores library mode after collection history` |

## Review limits

Targeted manual review: `model-grid.tsx` URL/filter/saved-view/browse/snapshot/selection
sections; `filter-sidebar.tsx` mode controls and outliner query ownership;
`lib/navigation.ts`, `lib/search-filters.ts`, `lib/last-collection.ts`, shared
SavedView types, their affected tests, and `tests/e2e/vault.spec.ts`.
The entire large grid is not yet manually reviewed. The historical ledger remains
explicitly incomplete. M2 still needs one normalized filter codec across all
library consumers; later increments replace the old list endpoints and remote
saved-view owner. The broad M2 goal is not closed by this checkpoint.

Browser startup exposed pre-existing Vite dependency-scan warnings in
`scripts/viewer-representation-pilot/browser.ts` for missing comparison-camera
and thumbnail-camera imports. Browser assertions passed; the warning remains an
M10 configuration/tooling finding, not a clean startup claim.

# M5 Library history ownership

M5 follows the server browse and conditional-write foundations. The pre-refactor Model detail Back link reconstructs only the collection, while Model cards omit the originating filters entirely. Multipart links carry a URL but create another entry when returning. The grid scrolls an inner `main` (and a second list container), so window scroll restoration cannot recover the reading position. The pre-refactor coherent visual snapshot carries rows and folder labels but not its originating URL; a retained card clicked during a pending navigation can capture the destination's filters.

The navigation owner will bind the displayed snapshot to its canonical URL and history entry. Detail links carry a safe URL fallback plus an in-memory entry identity. A normal Back action reuses the exact originating history entry when it is still known to this session; direct/deep links and new tabs use the URL fallback. Native modified-link gestures remain native. Restoration metadata contains entry identity, container offsets, visible entity anchors and loaded-page counts, never another copy of server data. It is bounded to the previously loaded pages and retired on session/access-scope change. Query remains the remote-data owner.

Implementation order: first qualify exact source links and snapshot identity, then inner-container restoration, bounded reconstruction, removed anchors and private retirement. React Router remains the navigation owner. No schema or deployment change is needed. Rollback removes this one navigation owner and its link integration; it does not alter stored library data.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | returns a Model to its exact Library view | Happy | Open filtered/sorted Model then detail Back | Same URL and history entry | Browser | ✅ |
| 2 | returns a Multipart set to its exact Library entry | Happy | Open Multipart then detail Back | Same URL and history entry | Browser | ✅ |
| 3 | preserves native modified-link navigation | Edge | Ctrl/Meta/middle/new-tab interaction | Native click remains unhandled for Ctrl/Meta/middle; usable encoded return URL | Component | ✅ |
| 4 | retains a displayed snapshot's return target during navigation | Edge | A displayed while B remains pending | A card returns to A; labels/cards remain coherent | Component | ✅ |
| 5 | restores the grid's nested reading position | Happy | Cached grid, scroll, detail, Back/Forward on desktop/mobile | Same visible entity and container offsets | Browser | ✅ |
| 6 | restores list layout independently | Happy | Cached list, scroll, detail, Back/Forward on desktop/mobile | List reading position restored | Browser | ✅ |
| 7 | reconstructs only the previously loaded pages after cache eviction | Edge | Evicted Query; saved entry with N pages | At most N pages; anchor restored if available | Feature/browser | ❌ |
| 8 | resets clearly when the old anchor cannot be restored | Error | Deleted anchor or incompatible cursor | Bounded recovery then visible reset | Feature/browser | ❌ |
| 9 | preserves the reading anchor after confirmed favourite removal | Edge | Visible favourite removed after ACK | Next surviving content stays at its offset | Browser | ❌ |
| 10 | retires private history metadata on session/access change | Edge | Session retired while detail open | No old restoration/cache/DOM reused | Feature/browser | ❌ |
| 11 | chooses a safe fallback for direct detail links | Edge | No known history origin or unsafe return URL | Same-origin Library URL; no external redirect | Component | ✅ |
| 12 | resolves rapid navigation using one displayed identity | Edge | A→B→C with out-of-order reads | Heading, cards, link target and restoration belong together | Component/browser | ❌ |

This matrix is the pre-implementation contract. No scroll/history acceptance is claimed from the earlier folder-navigation tests alone.

## Qualified increment: originating links

Model and Multipart detail now reuse the immediate originating history entry on an ordinary Back click, preserving the complete canonical URL. A retained result uses its settled snapshot identity while a different route is loading. Unknown/deep links use a validated same-origin Library fallback. Only the bounded entry registry is implemented here; scroll metadata and restoration remain pending.

Both browser cases failed before the link change (Model lost filters; Multipart pushed a new entry) and passed afterward: 2 Chromium cases in 12.7s. The link and entry mirrors passed 16 cases in 5.37s; the prior grid gate passed all 154 cases, including the retained-snapshot return target. App/UI/domain typecheck and lint passed. The session-retirement regression failed before the mount-session fence and passed afterward; row 10 remains open because full restoration/DOM retirement has not been exercised. JSDOM reports its expected unsupported native-document navigation for the three unhandled modified-click cases. No scroll or performance improvement is claimed.

## Qualified increment: cached reading position

The entry registry now owns a pair of numeric scroll offsets per layout. A feature hook observes the actual main/list containers, saves offsets on scroll and before link navigation, then restores on a settled entry's mount. These listeners do constant work per event; they do not inspect every card or introduce a timer, server-data copy, or another fetch cache. The same bounded registry clears on auth/access-scope retirement and rejects late writes from a retired view.

The desktop grid/list regressions both failed before implementation: returning placed the selected entity 3,626px and 1,343px away from its original viewport position respectively in those fixtures. Both passed after implementation (12.6s). The expanded four desktop/mobile cases then passed in 35.1s, asserting the exact original container offsets and the selected entity's viewport position after UI Back and browser Forward/Back.

This increment depends on Query retaining the rendered pages. Cache-eviction reconstruction, semantic anchors for deleted/reordered results, confirmed favourite removal and rapid route races remain separate open matrix rows. Pixel offsets alone do not close those contracts, and no production performance improvement is claimed.

The final affected navigation/entry/reading/grid/caption/hygiene gate passed 184 cases across six files in 98.95s. App/UI/domain typecheck, lint, format:check (715 files), and whitespace checks passed. The reading mirror also asserts that a retired view cannot rewrite offsets for the next session; full private-DOM/browser retirement remains open in row 10.

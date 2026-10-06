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

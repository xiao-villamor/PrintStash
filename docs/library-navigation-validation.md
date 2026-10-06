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
| 7 | reconstructs only the previously loaded pages after cache eviction | Edge | Evicted Query; saved entry with N pages | At most N pages; anchor restored if available | Feature/browser | ✅ |
| 8 | resets clearly when the old anchor cannot be restored | Error | Deleted anchor or incompatible cursor | Bounded recovery then visible reset | Browser | ✅ |
| 9 | preserves the reading anchor after confirmed favourite removal | Edge | Visible favourite removed after ACK | Next surviving content stays at its offset | Browser | ✅ `tests/e2e/library-navigation.spec.ts::preserves the grid/list reading anchor after removing a confirmed favorite` (available Model/Multipart gestures) |
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

## Bounded reconstruction and stable geometry

A reading bookmark now records model/folder page counts plus an optional semantic entry key and its offset within the actual container. After Query garbage collection, restoration requests only missing pages up to those recorded counts. It never follows an unvisited third page to search for an absent anchor. A removed anchor, failed continuation or an unavailable offset resets the affected view to its start with an explicit localized notice. The browser cases advance the real Query GC clock; they do not inject a replacement server-state cache.

All three new browser scenarios failed before this increment. The first implementation exposed two further integration defects: an unchanged, queued scroll erased a click's semantic anchor, and entrance transforms shifted measurements by 7–8px after restoration. The queued-event probe confirmed the first. A controlled animation-disabled run passed; restoring animations reproduced the offset mismatch. The final fix preserves the anchor when coordinates have not changed and removes entrance animation from Library route results, following DESIGN.md's navigation rule. Press feedback remains. The old grid-delay helper/assertions were removed, and the mixed-order browser assertion now selects semantic article surfaces.

The full nine navigation cases passed, including desktop/mobile cached Back/Forward and all three GC scenarios. The accompanying motion file first exposed an incorrect fixture assumption (root has one Model plus folders, not multiple articles); its fixture now explicitly supplies two Models and one Multipart set. Both motion preferences and the affected mixed-order pagination case passed together (3 cases, 25.7s). All temporary diagnostic instrumentation and animation overrides were removed. These are correctness results, not production performance measurements.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 13 | bounds history reconstruction when its anchor is available | Edge | Actual five-minute Query GC after two model pages | Original anchor position restored; only visited continuation requested | Browser | ✅ |
| 14 | bounds history reconstruction when its anchor is removed | Error | GC plus removal of the clicked entity | Explicit reset to top; no unvisited continuation | Browser | ✅ |
| 15 | bounds history reconstruction when its anchor is stale | Error | Restoring continuation returns 409 | Explicit reset; no automatic continuation loop | Browser | ✅ |
| 16 | Library route results stay still with each motion preference | Edge | Models and Multipart cards in Library | No entrance transforms move restored layout | Browser | ✅ |
| 17 | bounds folder reconstruction after eviction | Edge/error | Evict two loaded folder pages; return with success or 409 | At most the saved page count; recovery or reset | Component + real query/HTTP adapters | ✅ |

The nine navigation cases plus the unchanged layering case passed in the initial combined run; that run also contained the two incorrect-fixture failures described above. The targeted corrected motion/pagination run closes those failures without claiming that initial run was wholly green. Favorite-removal anchors, explicit refresh reconstruction, rapid destination races and full browser session retirement still keep M5 open.

Final affected gate: 290 tests across 11 files passed in 69.07s, including both folder reconstruction outcomes and the integrated Documents/Inbox/Profiles session corrections. Full app/UI/domain typecheck passed. Lint initially found the obsolete helper's unused expect import; it was removed. The full browser contract remains M5-partial as described above.

## Retired favorite acknowledgements

Before changing the favorite owner, a late acknowledgement must be fenced before
any cancellation in the current Query client, as well as before publishing data.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | leaves a new browse read active after a retired favorite acknowledgement | Edge | Server ACK held before success callback; session retires; new browse GET starts | New request remains live and its list is displayed | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::leaves a new browse read active after a retired favorite acknowledgement` |

The regression failed before the pre-cancellation session guard: the next browse
request had an aborted signal. After the guard, all 10 favorite-owner tests passed
within the 27-case preferences/hygiene integration gate (3.43s). Domain preference
mirrors independently passed all 29 cases (1.85s).

## Confirmed favorite removal geometry

Preserve the next visible surviving item's vertical reading position after the
acknowledged favorite disappears. Test both nested list and grid containers in a
real browser before deciding whether additional coordination is necessary.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | preserves the grid reading anchor after removing a confirmed favorite | Edge | Scrolled favorites; pending unstar then ACK | Pending card remains; ACK removes it; next visible survivor keeps its offset | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::preserves the grid reading anchor after removing a confirmed favorite` |
| 2 | preserves the list reading anchor after removing a confirmed favorite | Edge | Scrolled favorites; pending unstar then ACK | Detail remains while pending; ACK removes the favorite from the list; Back preserves the next survivor offset | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::preserves the list reading anchor after removing a confirmed favorite` |

The direct grid gesture passed before any change. The list has no inline favorite
action, so its regression uses the actual detail action then Back. That flow failed:
the surviving row returned at1821px instead of424px because the deleted anchor
caused a reset. Retain one adjacent semantic anchor in history metadata; only an
acknowledged own removal in a Favorites entry may promote it. External deletion
keeps the existing explicit-reset contract.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 3 | promotes a neighboring anchor only in the affected Favorites entry | Edge | Favorite ACK for the recorded anchor; Everything and another item also recorded | Affected saved reading position uses its neighbor; other records unchanged | Frontend unit | ✅ `src/features/library/__tests__/navigation-state.test.tsx::promotes a neighboring anchor only for the affected $label` |
| 4 | retains explicit reset when the removed favorite has no neighbor | Edge | One-item Favorites reading position | Missing primary remains available for the existing reset decision | Frontend unit | ✅ `src/features/library/__tests__/navigation-state.test.tsx::retains explicit reset when the removed favorite has no neighbor` |
| 5 | rejects a retired favorite receipt for reading metadata | Edge | New session has its own position; previous receipt arrives | New position remains unchanged | Frontend unit | ✅ `src/features/library/__tests__/navigation-state.test.tsx::rejects a retired favorite receipt for reading metadata` |

The first neighbor implementation exposed an additional geometry bug: the list
container had no scroll range, while main owned the1400px offset. Correcting list
scrollTop was clamped to zero and left a65px jump. Capture now selects the actual
scroll container. Both favorite browser cases pass (15.9s); temporary tagged
instrumentation was removed. This is correctness evidence, not a performance
comparison. Explicit Refresh reconstruction and rapid navigation remain open.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 6 | preserves the grid reading anchor after removing a confirmed favorite Multipart | Edge | Scrolled Multipart favorites; pending unstar then ACK | Pending card remains; ACK removes it; next survivor keeps its offset | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::preserves the grid reading anchor after removing a confirmed favorite Multipart` |

## Rapid destination changes

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | keeps breadcrumbs with the displayed result across rapid destinations | Edge | Folder A settled; root B delayed; child C requested before B arrives | A heading, breadcrumb and return link stay paired until all C data arrives; late B never replaces C | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::keeps breadcrumbs with the displayed result across rapid destinations` |

The rapid-navigation regression failed with a missing Parts breadcrumb while its
cards were still displayed. The breadcrumb now derives entirely from the settled
snapshot, independent of the requested destination. All18 focused folder/history
cases passed11.74s. This covers the component/Query boundary; the original broader
rapid-navigation browser row remains open.

Final favorite qualification:11 full navigation browser cases passed1.2m; the
expanded three available favorite gestures (Model grid/detail, Multipart grid)
passed20.8s. Multipart's initial2px difference was hover translation when the
neighbor moved under the pointer; comparison now uses the same unhovered state
without changing product motion. The five-file navigation/grid/hygiene gate
passed185tests68.60s before the one-line breadcrumb fix; its affected18cases were
then rerun as above. No production performance improvement is asserted.

## Explicit refresh reconstruction

Refresh restarts from page one using the new server revision, while retaining a
bounded reading bookmark. Its completion must not restore a retired route/session.
Browser activation invokes the real Refresh button without moving focus/scroll,
so the assertion isolates its reading contract from where the toolbar is placed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | bounds explicit refresh when its anchor is available | Edge | Two pages read; new revision inserts an earlier result | Same visible anchor offset; new second cursor only, no unvisited third page | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::bounds explicit refresh when its anchor is available` |
| 2 | bounds explicit refresh when its anchor is removed | Error | Two pages read; new revision removes captured anchor | Clear reset notice after bounded reconstruction | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::bounds explicit refresh when its anchor is removed` |
| 3 | bounds explicit refresh when its anchor is stale | Error | New first page; continuation returns 409 | Clear reset notice; no third-page search | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::bounds explicit refresh when its anchor is stale` |
| 4 | ignores refresh restoration after navigation retires its entry | Edge | Refresh awaits response; route changed | New entry does not inherit old scroll/page demand | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::ignores refresh restoration after navigation retires its entry` |
| 5 | ignores refresh restoration after session retirement | Edge | Refresh awaits response; auth changes | No old bookmark resurrected | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::ignores refresh restoration after session retirement` |
| 6 | ignores refresh restoration after layout change | Edge | Refresh awaits response; grid changes to list | List offset and page demand stay independent | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::ignores refresh restoration after layout change` |
| 7 | ignores completion of a superseded refresh | Edge | Two refreshes finish out of order | Only latest bookmark restores after its own completion | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::ignores completion of a superseded refresh` |
| 8 | resets reading position when refresh replacement rejects | Error | First replacement fails before data is ready | Reset status and zero offsets; no continuation | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::resets reading position when refresh replacement rejects` |

The three browser regressions were confirmed red before this checkpoint: the
available anchor moved from −118px to 0px, while removed/stale anchors lacked the
reset notice. Refresh now snapshots the existing reading metadata before replacing
the query, then uses the same bounded reconstruction as history restoration.
Route, layout, session and newer-operation fences retire late completions; no
remote-data cache or timer was added. The focused three-case browser run passed
in 38.0s; the hook's 10 cases passed in 6.19s.

An initial concurrent regression run recorded 179 passed / 2 failed across 181 Vitest
cases: an unrelated PWA spec contract header (fixed by its owner) and the existing
mesh-upload dialog's 5000ms timeout. Its browser run recorded 14 passed / 1 failed: the
existing mobile-grid history test exceeded its 30000ms total near Back navigation,
before its geometry assertions. The timeout artifact was retained locally. These
are recorded failures, not green runs; serial confirmation uses unchanged limits
and assertions.

This closes only the explicit Refresh reading-position checkpoint, not M5 as a
whole. A separate source-review finding remains unverified: Refresh replaces browse
pages but does not refetch the folder-page or selected-folder projections, so a
Model ingestion/deletion may leave displayed folder counts stale. That projection
freshness question is outside this checkpoint's reading-position contract.

Serial browser confirmation passed all 15 navigation cases in 2.1m, including the
mobile-grid case in 12.4s. That case also passed alone in 11.9s (15.7s total).
The unchanged mesh-upload case passed alone in 1.45s (12.09s total; 154 cases
excluded by the name filter). No timeout, assertion or production code changed
between the concurrent failures and these confirmations. Full lint, formatting
(730 files) and app/UI/domain typechecks passed after the capture callback's
required dependency was made explicit.

Final serial affected gate: all 181 tests passed across reading-position,
navigation-state, ModelGrid and suite-hygiene (4 files, 102.47s). All eight rows
above now have named assertions. The earlier timeouts did not reproduce in the
isolated checks or the serial complete affected gates; they remain documented as
observed timing failures rather than claimed product fixes.

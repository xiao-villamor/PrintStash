# Library refresh projection validation

Explicit Library refresh must replace one coherent displayed snapshot: model/multipart rows, the current folder page or search results, and the selected folder lookup that supplies its name and breadcrumbs. Existing Query entries remain the only remote cache. The existing settled snapshot remains visible until every required current projection is ready; a small entry/session-bound refresh incarnation coordinates publication and recovery, without copying another remote snapshot.

Refresh cancels obsolete current Query reads, rechecks the selected path before choosing its child-page key, and restarts current model/folder pagination from the first page. Root refresh reads root children; search refresh reads the active search projection. Unrelated folder pages are not swept. A transient failed required read retains the coherent display and exposes an explicit retry. A lookup 404 retires the private old snapshot: `collection_tree.lookup` filters by accessible IDs and returns `collection_not_found` for a missing or inaccessible path, so it cannot be treated as a transient outage. Navigation and session retirement invalidate pending completion. Authority notices still require a user gesture, and existing navigation/reading-position semantics remain unchanged.

Owned paths: `frontend/src/components/model-grid.tsx`, its adjacent test, the existing explicit-refresh cases in `frontend/tests/e2e/library-navigation.spec.ts`, and this document. No new query owner, shared cache, generic coordinator or API change is planned.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | waits for refreshed folders before replacing models | Edge | Model refresh resolves before deferred root children | Old models/folders remain together until both finish | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::waits for refreshed folders before replacing models` |
| 2 | waits for refreshed models before replacing folders | Edge | Child refresh resolves before deferred model page | Old models/folders remain together until both finish | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::waits for refreshed models before replacing folders` |
| 3 | refreshes the selected folder lookup | Happy | Ancestor/name/count change at same path | New breadcrumb and folder rows publish with model page | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::refreshes the selected folder lookup` |
| 4 | follows the refreshed folder identity | Edge | Lookup path resolves to a replacement identity | Children read uses confirmed replacement ID | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::follows the refreshed folder identity` |
| 5 | refreshes active folder search results | Happy | Search match list changes | Current search projection updates with models | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::refreshes active folder search results` |
| 6 | restarts refreshed folder pagination | Edge | Two folder pages loaded before refresh | Only fresh first page remains; old cursor not replayed | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::restarts refreshed folder pagination` |
| 7 | retries a failed folder refresh | Error | Children refresh returns 503 | Old coherent result retained; explicit Retry completes fresh result | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retries a failed folder refresh` |
| 8 | retries a failed lookup refresh | Error | Selected lookup refresh returns 503 | Old name/breadcrumbs retained; explicit Retry recovers | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retries a failed lookup refresh` |
| 9 | ignores a refresh across rapid destinations | Edge | Deferred refresh followed by two rapid folder destinations | Late old result cannot replace destination | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::ignores a refresh across rapid destinations` |
| 10 | retires a refresh with its session | Edge | Session retires during refresh | No private snapshot or stale recovery publishes | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retires a refresh with its session` |
| 11 | keeps displayed rows until explicit refresh | Happy | Authority notice announces a new revision | Existing authority behavior remains unchanged | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx` (full regression) |
| 12 | restarts a rejected continuation from the first page | Edge | Browse cursor returns 409 | Existing first-page reset behavior remains unchanged | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx` (full regression) |
| 13 | keeps folder metadata with the displayed snapshot | Edge | Refreshed lookup returns new tags before models settle | Old folder tags remain until the complete refresh publishes | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::keeps folder metadata with the displayed snapshot` |
| 14 | retires a missing folder snapshot | Error | Explicit lookup returns inaccessible-or-missing 404 | Private old rows, heading, breadcrumb and folder metadata stay absent through Retry | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retires a missing folder snapshot` |
| 15 | bounds explicit refresh reading-position recovery | Edge | Available, removed or rejected reading anchor | Existing browser cases restore the anchor or explicitly reset, with bounded continuation | Playwright | ✅ `tests/e2e/library-navigation.spec.ts::bounds explicit refresh when its anchor is available/removed/stale` |
| 16 | puts each model back where it came from | Happy | Move succeeds, then the current move toast Undo is clicked | Exactly two move commands use the destination then origin, with acknowledged version for Undo | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::puts each model back where it came from` |

## Validation

The matrix preceded tests and production edits. The first RED reproduced eight missing-refresh/recovery behaviors; navigation and session retirement already passed. The first correction passed those ten cases. Two added REDs then proved that folder tags could advance before rows and that lookup 404 retained a private snapshot; the correction now renders folder metadata from the existing snapshot and retires it on confirmed lookup denial.

- Focused refresh projection cases, including strengthened rapid-navigation and denied-heading/breadcrumb assertions: 12 passed (17.91 seconds).
- Final coordination-metadata lifetime checks: 2 passed (7.03 seconds).
- Full frontend lint: passed without suppressions. The metadata ref is captured inside the layout effect so cleanup does not read a mutable ref directly.
- App, UI and domain TypeScript checks: passed. An initial generic inferred a null-only cursor and an error formatter expected a code rather than a caught value; both were corrected using the existing cursor contract and error boundary.
- Complete ModelBrowser regression plus suite hygiene: 181 passed (177 behavior cases and four hygiene checks; 62.79 seconds).
- Existing explicit-refresh browser qualification: all three available/removed/stale-anchor cases passed (19.8 seconds).
- Final app/UI/domain types, full lint, owned formatting and whitespace checks: passed.

The first complete ModelBrowser/hygiene run passed 180 cases and failed one existing Undo assertion. An isolated diagnostic captured the selected `Moved 1` toast and both correct move requests, followed by an unrelated event-ticket POST with an empty body. The old assertion selected the final POST of any kind. Its correction asserts the move toast identity and exactly both move-endpoint payloads, including the acknowledged version; no production Undo change was needed.

The first browser qualification passed the removed/stale-anchor cases but the available-anchor assertion completed against the intentionally retained old snapshot before its fresh continuation. Its corrected acceptance waits for the exact fresh cursor requests and the inserted fresh row to attach, without scrolling, before asserting restored anchor geometry. The final bounded request assertion and original timeouts are unchanged.

Named follow-up gap: `src/lib/api/taxonomy.ts` collection children/lookup/search readers and `src/lib/queries.ts` collection hooks do not consume Query AbortSignal. This checkpoint uses Query cancellation to fence obsolete publication, but does not claim native HTTP cancellation: those reads can continue until shared session retirement. A later canonical-options/signal-port change must migrate all consumers together.

The existing background refetch policy of shared collection hooks remains unchanged. This checkpoint coordinates explicit current-projection refresh and does not claim global sidebar/history cache retirement on a folder-specific 404. Full repository integration and remote CI remain the parent task's responsibility.

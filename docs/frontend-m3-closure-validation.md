# M3 ordered browse qualification

M3 is closed locally after M0–M2. M4 is next. This closes the ordered browse
contract and its first-party consumer acceptance; it does not close conditional
editing, history restoration, startup performance or final PR/CI delivery.
The [server contract record](library-contracts-validation.md),
[browse client record](library-browse-client-validation.md) and
[authority integration](library-authority-ui-validation.md) retain the broader
behavior inventory and earlier regression evidence.

## Outcome and ownership

The Library backend filters authorized live entries and globally orders both
Models and Multipart Sets before pagination. Its signed cursor binds the caller,
normalized view/filter/sort/page-size inputs and transactional catalog revision.
The implementation uses a revision-bound offset, not keyset pagination or a
historical snapshot. Concurrent catalog changes reject continuation explicitly.

The frontend consumes the server sequence through the Library Query owner. It
retains displayed pages on a continuation failure, offers retry, and requires an
explicit refresh after a revision change. Separate Model/Multipart downloads,
arbitrary group caps and sorting a fetched prefix no longer determine grid
membership/order. This is an ownership change; reduced file size is not evidence.

Acceptance review found one further production defect: `selectAllMatching` sent
`limit=500` to a browse endpoint that accepts 1–100. It now reuses the existing
browse page size. The HTTP test fake independently enforces the published bound.
All-matching selection traverses opaque cursors, skips empty intermediate pages,
selects only Model identities and preserves prior selection when continuation
fails. Batch-command limits remain a separate contract.

## Additional acceptance matrix

These requirements were recorded before adding or correcting their tests. Status
records named assertions; execution results follow separately.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | selects every model through valid browse pages | Happy | Mixed first page and later Model; HTTP enforces limit 1–100 | Two Model identities selected, continuation consumed, set not selected | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::selects every model through valid browse pages` |
| 2 | selects models beyond an empty continuation page | Edge | Same list with an empty intermediate page carrying another cursor | Later Model selected; advertised continuation consumed | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::selects models beyond an empty continuation page` |
| 3 | preserves selection when continuation becomes stale | Error | Prior selection, first new page succeeds, next returns 409 browse_refresh_required | Prior selection remains; partial new selection is not published | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::preserves selection when continuation becomes stale` |
| 4 | retries a failed continuation without discarding read pages | Error | First page visible; continuation temporarily rate limited; user retries | First page stays visible, error appears, retry appends the later page | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retries a failed continuation without discarding read pages` |
| 5 | rejects continuation after each catalog dependency writer | Edge | Real row from each registered table, prior cursor, committed SQL update | Exactly one row updated; prior cursor rejected with browse_refresh_required | Integration | ✅ `integration/db/test_library_contracts_v1.py::TestInstall::test_rejects_continuation_after_each_catalog_dependency_writer` |
| 6 | denies unauthenticated library browsing | Error | No cookie or bearer credential; browse request | 401 and no private card payload | Integration | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_denies_unauthenticated_library_browsing` |
| 7 | answers a late mixed browse page within budget | Edge | Supported library plus 10,000 sets; admin/viewer; name/date sort; valid tail cursor | Authorized tail contains 60 items, advertises its final continuation, median request latency ≤ existing 1.5 s browse budget | Integration scale | ✅ `repo/test_read_scale_budgets.py::TestLibraryReadsAtScale::test_answers_a_late_mixed_browse_page_within_budget` |
| 8 | pages equal names by kind then identity | Edge | Real PostgreSQL, two Model names plus set name fold equally, ascending/descending | Three one-item pages keep the same kind/id tie order without duplicates | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestLibraryBrowse::test_pages_equal_names_by_kind_then_identity` |
| 9 | places missing metrics after measured Models | Edge | Real PostgreSQL, measured Model, empty set; five metric sorts | Measured Model precedes null set across page boundary | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestLibraryBrowse::test_places_missing_metrics_after_measured_models` |
| 10 | filters readable members before pagination | Error | Real PostgreSQL, readable set references hidden STL Model, valid set references readable STL Model | Hidden match excluded; permitted Model/set remain reachable with exact total | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestLibraryBrowse::test_filters_readable_members_before_pagination` |
| 11 | pages mixed dates in global order | Happy | Real PostgreSQL, old Model, middle set, new Model; ascending/descending | Three one-item pages follow global dates without duplicates | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestLibraryBrowse::test_pages_mixed_dates_in_global_order` |
| 12 | refreshes a changed library deliberately | Edge | Existing page has continuation, authority announces a new revision | Load more unavailable until refresh; original card retained; replacement starts without cursor | Playwright | ✅ `tests/e2e/vault.spec.ts::refreshes a changed library deliberately` |
| 13 | excludes trashed candidates before pagination | Edge | PostgreSQL, live Model/set plus trashed Model and trashed-folder candidates | Only two live identities remain across one-item pages, exact total and final cursor | Integration PostgreSQL | ✅ `integration/postgres/test_library_browse.py::TestLibraryBrowse::test_excludes_trashed_candidates_before_pagination` |

## Correctness qualification

- Bulk selection: 6/6 passed (22.34 s). The initial RED filter selected only the
  stale-continuation scenario, which failed because the server fake rejected
  limit 500. Vitest did not select the two computed parameter names; they are
  not claimed as observed RED. Continuation retry passed separately (7.42 s).
- Trigger installation and actual writers: 68/68 passed (14.29 s), including one
  committed row update for each of the 32 registered catalog dependencies.
  Existing Model cases also cover INSERT/UPDATE/DELETE and transaction rollback;
  this sweep does not claim all three actions were executed for every table.
  The first sweep had 31 passes and one fixture error: provenance requires
  `title`, not `name`. The fixture was corrected without weakening validation.
- Anonymous browse: 1/1 passed (4.85 s). The inaccessible-entry case also now
  asserts total zero, closing the explicit no-count-leak requirement (5.89 s).
- Chromium changed-list continuation: 1/1 passed (14.3 s). Load more is present
  initially, unavailable after a changed revision, and the explicit refresh
  starts without a cursor while the old card remains stable until replacement.
- PostgreSQL additions: 10/10 passed (232.68 s, exit 0, unchanged source manifest).
  The existing real-service/fresh-migration fixture verifies both date directions,
  both name tie directions, five metric sorts and readable-member filtering.
  A final explicit live/trashed PostgreSQL case also passed separately (exit 0),
  asserting that trashed candidates cannot consume the page or inflate its total.
  Earlier related PostgreSQL qualification remains in the server record.
- Ordinary backend gate: 19,226 passed, one failed (2,588.87 s, exit 1). Its only
  failure was five test names containing `_and_`; these listed ordering,
  visibility, version-preserving transfer or rollback outcomes. Renaming them
  preserved all assertions. The naming gate then passed (8.20 s). This is a
  broad run plus a verified correction, not a single all-green invocation.
- Ordinary migrations/contracts: the retained 174 named passes plus the disjoint
  208-case recovery exactly cover the 382 collected cases. The original process
  had no terminal result; the recovery exited 0. All 1,752 recorded backend Python
  sources matched at recovery qualification. This is partitioned evidence.
- Static checks: frontend app/UI/domain type checks, affected component/browser
  lint and formatting passed. Backend `ruff check app/ tests/`, required format
  scope (175 files), and Pyright (zero errors/warnings) passed.

Production `app/` and Alembic sources stayed unchanged throughout the ordinary
run. Six test files changed: the browse, transfer and restore tests received
naming/additional authorization assertions; the excluded contracts, PostgreSQL
and scale files gained the explicit acceptance cases above. New assertions were
qualified separately. Both additional PostgreSQL and scale runs recorded no
source changes. Their raw commands, logs, results and source hashes are retained
in the local M3 evidence archive.

## Backend performance acceptance

The deep-page test uses the existing factory with 25,000 collections, 100,000
Models and 10,000 Multipart Sets. An initial real request supplies the cursor's
caller/filter/revision binding. The production codec seeds a test-only offset
near the tail (total minus 120), avoiding timing a thousand preceding requests.
This measures the late request, not cumulative navigation or a historical view.

One warm-up and three retained samples run serially per combination, after the
other suites exit. Existing host services remain; this is not dedicated hardware.
Every response contains 60 authorized entries, exact total and final continuation.
The unchanged budget is median ≤1.5 s. All four cases passed in 41.71 s (exit 0).

| Reader | Sort | Retained samples (ms) | Median (ms) |
|---|---|---|---|
| Administrator | name ascending | 311.70, 320.53, 314.45 | 314.45 |
| Administrator | date descending | 217.63, 223.16, 486.37 | 223.16 |
| Granted viewer | name ascending | 382.14, 382.25, 384.50 | 382.25 |
| Granted viewer | date descending | 287.90, 279.64, 291.49 | 287.90 |

The prior first-page/growth measurements remain distinct. Constant statement and
bound-parameter tests (including Multipart projection) passed in the ordinary
lane. The late-page result supports the current offset strategy at the measured
size; it is not evidence of an overall frontend speedup. Render, image decoding,
request contention and before/after frontend distributions remain M6/M11.

## Boundaries of closure

M3's reviewed assertions and scoped acceptance are complete. The final complete
resource suite and required CI for the final commit remain M11 delivery gates.
The broad run's naming failure and its targeted correction remain visible.
Global revision invalidation can reject continuation during active writes;
performance under ingestion remains an explicit later measurement. Legacy
headerless edit compatibility and restore/edit-version semantics belong to M4,
not this browse closure. No required behavior is silently waived here.

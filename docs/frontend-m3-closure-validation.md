# M3 ordered browse qualification

M0–M2 are closed; only M3 is active. Its existing implementation and evidence are
in [the server contract record](library-contracts-validation.md),
[the browse client record](library-browse-client-validation.md) and
[authority integration](library-authority-ui-validation.md). This record reconciles
remaining M3 acceptance; it does not close M4 conditional editing or M5 history.

## Confirmed integration gap

`ModelBrowser::selectAllMatching` sends `limit=500` to `/api/v1/models/browse`.
`BrowseQuery.limit` permits 1–100. The existing test only asserted that a request
contained 500; its permissive HTTP fake never rejected that unsupported request.
The intended outcome is complete Model selection through valid opaque cursor
pages, retaining prior selection if the server rejects continuation. Multipart
cards must not become Model IDs. The implementation should use the existing
endpoint within its published limit, without adding a second list/cache owner.

## Requirement matrix (before test correction)

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | selects every model through valid browse pages | Happy | Mixed first page and later Model; HTTP enforces limit 1–100 | Two Model identities selected, continuation consumed, set not selected | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::selects every model through valid browse pages` |
| 2 | selects models beyond an empty continuation page | Edge | Same list with an empty intermediate page carrying another cursor | Later Model selected; advertised continuation consumed | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::selects models beyond an empty continuation page` |
| 3 | preserves selection when continuation becomes stale | Error | Prior selection, first new page succeeds, next returns 409 browse_refresh_required | Prior selection remains; partial new selection is not published | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::preserves selection when continuation becomes stale` |

Production change is limited to the request size at the existing all-matching
consumer. Its tests use the real component, Query and endpoint with HTTP stood in
for. Existing backend query validation supplies the independently defined upper
bound. Batch command chunk sizes remain separate endpoint contracts.

## Backend gate underway

The prior migration invocation ended without a terminal result after 174 named
passes. Its disjoint recovery completed 208/208; the union is exactly the 382
collected ordinary migration/contracts cases. All 1,752 recorded backend Python
files still match their hashes. This is partitioned evidence, not a single full
suite result. The current official `full-ordinary` lane excludes exactly those
two already-qualified paths and runs with four workers, no concurrent migrations
or resource suites, and a terminal result/source manifest. Remaining resource
qualification is separate. Do not report this running gate as green.

## Additional acceptance gaps found by assertion review

The existing shared trigger inventory proves registration for every dependency
and executes INSERT/UPDATE/DELETE on Models, but it does not yet execute a writer
for every registered table and observe stale continuation. Keep that master row
open until its behavior coverage is established. The browse route's authentication
dependency is present; an explicit unauthenticated browse assertion is still needed
(the existing test covers the revision endpoint). These are test gaps, not evidence
that authorization or a particular trigger is broken.

The installed cursor is a signed revision-bound offset. The plan's keyset warning
correctly excludes historical snapshot guarantees, but the current implementation
must not be described as keyset pagination. Existing scale evidence covers the
first browse page; deep continuation cost remains unmeasured and must be assessed
before the M3 performance claim is accepted.

A separate coverage gap concerns recoverable continuation errors. The old 409
assertion covers a required refresh, not ordinary retry. Add this existing required
behavior before calling the master row covered:

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 4 | retries a failed continuation without discarding read pages | Error | First page visible; continuation temporarily rate limited; user retries | First page stays visible, error appears, retry appends the later page | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retries a failed continuation without discarding read pages` |

The writer assertion will mutate an actual persisted row for each table in the
reviewed dependency inventory, then request continuation with the prior cursor.
Fixtures retain real foreign keys and enum/check constraints. Existing per-action
Model cases continue to cover INSERT/UPDATE/DELETE and transactional rollback;
this additional sweep observes continuation rejection across all registered
writer tables rather than only inspecting trigger names.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 5 | rejects continuation after each catalog dependency writer | Edge | Real row from each registered table, prior cursor, committed SQL update | Exactly one row updated; prior cursor rejected with browse_refresh_required | Integration | ✅ `integration/db/test_library_contracts_v1.py::TestInstall::test_rejects_continuation_after_each_catalog_dependency_writer` |
| 6 | denies unauthenticated library browsing | Error | No cookie or bearer credential; browse request | 401 and no private card payload | Integration | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_denies_unauthenticated_library_browsing` |

## Deep continuation measurement plan

Use the existing supported-scale factory (25,000 collections, 100,000 Models),
plus 10,000 Multipart Models so the mixed endpoint exercises both projections.
Test admin and inherited viewer access with name-ascending and date-descending
sorts. Seed a valid late position with the production cursor codec after the first
real page supplies its caller/filter/revision binding. This setup measures a tail
request; it deliberately does not claim the time of walking all preceding pages.
Use one warm-up and three retained timings, serially on an otherwise quiet test
host, against the existing browse budget of 1.5 seconds. Do not run this during the
ordinary gate or another benchmark. This verifies the implemented offset strategy
instead of describing it as an unimplemented keyset strategy.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 7 | answers a late mixed browse page within budget | Edge | Supported library plus 10,000 sets; admin/viewer; name/date sort; valid tail cursor | Authorized tail contains 60 items, advertises its final continuation, median request latency ≤ existing 1.5 s browse budget | Integration scale | ❌ missing |

## PostgreSQL acceptance coverage

Existing PostgreSQL evidence covers Unicode order, revision/authorization changes,
rollback and migration. It does not assert paginated ties, metric null placement
or member-filter eligibility before pagination on that engine. Add those required
contracts using the existing real PostgreSQL fixture and production factories;
run them only after the ordinary gate, without a second container suite.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 8 | pages equal names by kind then identity | Edge | Real PostgreSQL, two Model names plus set name fold equally, ascending/descending | Three one-item pages keep the same kind/id tie order without duplicates | Integration PostgreSQL | ❌ missing |
| 9 | places missing metrics after measured Models | Edge | Real PostgreSQL, measured Model, empty set; five metric sorts | Measured Model precedes null set across page boundary | Integration PostgreSQL | ❌ missing |
| 10 | filters readable members before pagination | Error | Real PostgreSQL, readable set references hidden STL Model, valid set references readable STL Model | Hidden match excluded; permitted Model/set remain reachable with exact total | Integration PostgreSQL | ❌ missing |
| 11 | pages mixed dates in global order | Happy | Real PostgreSQL, old Model, middle set, new Model; ascending/descending | Three one-item pages follow global dates without duplicates | Integration PostgreSQL | ❌ missing |

## Qualification checkpoint

The selection request now reuses the browse page size instead of sending 500.
The initial RED invocation selected the stale-continuation scenario only: one
failure caused by the HTTP fake rejecting the invalid request. Vitest did not
select the two computed parameter names from that filter; those cases are not
claimed as observed RED. After the fix, all six bulk-selection cases passed
(22.34 s). The additional continuation retry case passed (7.42 s).

All 68 trigger installation/writer cases passed (14.29 s), including one committed
row update per registered catalog dependency. The first sweep had 31 passes and
one fixture error: provenance accepts `title`, not `name`. Correcting that fixture
retained the production validation. The anonymous browse assertion passed (4.85 s).

The ordinary gate found five test names containing `_and_`. They listed outcomes
of ordering/visibility, version-preserving transfer or transactional rollback.
Renaming them to those behaviors preserves every assertion. The targeted naming
gate now passes (8.20 s); the original broad run still records its naming failure.
The changed matrix references in the server contract record follow the new names.
PostgreSQL and tail-page measurements remain pending; M3 is not closed.

## Browser continuation gate

The existing browser flow asserts an explicit refresh from a changed revision,
but does not attempt or assert the availability of continuation while changed.
Strengthen that same behavior with the unavailable Load more control so the master
requirement has an observable browser assertion.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 12 | refreshes a changed library deliberately | Edge | Existing page has continuation, authority announces a new revision | Load more unavailable until refresh; original card retained; replacement starts without cursor | Playwright | ✅ `tests/e2e/vault.spec.ts::refreshes a changed library deliberately` |

Browser continuation acceptance passed in Chromium (1/1, 14.3 s). App/UI/domain
type checks and the changed component/test lint and formatting checks passed.

# Behaviour coverage and validation plan

Status: implementation qualification in progress. M2 rows 1–5 have reviewed
assertions and qualified executions, consolidated in the
[M2 closure record](../frontend-m2-closure-validation.md). M1 rows 28–31 are qualified in the
[M1 closure record](../frontend-m1-closure-validation.md). M3 rows 6–19 are reconciled in the [M3 closure record](../frontend-m3-closure-validation.md), including the ordinary gate correction, PostgreSQL additions and deep-page budget. M4 rows20–27 are reconciled in the [M4 acceptance record](../frontend-m4-closure-validation.md). M5 rows32–36 are qualified in the [M5 navigation record](../frontend-m5-closure-validation.md). Other rows remain the
original requirements pending reconciliation with their feature validation records;
`❌ missing` means no accepted reference has yet been attached here, not that no
test exists anywhere. This is not an assertion-by-assertion audit of the whole
suite. Expand the matrix per feature before implementation, especially settings,
provider forms, extension capture and viewers.

Use the exact repository columns and one observable behaviour per row. Existing
coverage becomes `✅ <tier dir>/<file>::<test>` only after reading its assertions.
A justified omission is `⏭️ N/A — reason`, never a skipped failing test. Runtime
results are recorded separately from coverage existence. For parameter sweeps,
each case must have the same assertion shape; otherwise split the row.

| #   | Behaviour (test name)                                    | Category | Precondition / input                             | Observable outcome asserted                                     | Tier            | Status     |
| --- | -------------------------------------------------------- | -------- | ------------------------------------------------ | --------------------------------------------------------------- | --------------- | ---------- |
| 1   | migrates a retired library mode                          | Edge     | Organized or Parts only in URL/preference        | Everything selected; unrelated URL inputs retained              | Frontend unit   | ✅ `src/features/library/__tests__/url.test.ts::normalizes retired URL mode` and `normalizes retired preference` |
| 2   | preserves Multipart Sets selection                       | Happy    | current multipart URL                            | only eligible groupings displayed                               | Playwright real | ✅ `tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible` |
| 3   | keeps referenced Models independently visible            | Happy    | Model referenced by two sets                     | one Model card remains accessible in Everything                 | Playwright real | ✅ `tests/e2e-real/multipart-models.spec.ts::keeps a shared Model independently accessible` |
| 4   | round trips a saved library view                         | Happy    | folder/filter/sort view saved                    | restored URL has the same normalized inputs                     | Frontend unit   | ✅ `src/features/library/__tests__/filters.test.ts::round trips complete Library filters`; real saved-view lifecycle in closure record |
| 5   | normalizes malformed view parameters                     | Error    | invalid enum or identifier                       | documented canonical URL without new history entry              | Frontend unit   | ✅ `src/components/__tests__/model-grid.test.tsx::replaces a malformed printer bookmark`; `src/features/library/__tests__/url.test.ts` |
| 6   | returns globally ordered mixed pages                     | Happy    | both entry kinds span pages                      | concatenated identities match canonical server order            | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_pages_without_duplicates` |
| 7   | breaks equal sort values deterministically               | Edge     | equal values; null values; Unicode names         | stable order across repeated page reads                         | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_orders_name_ties_by_kind_then_id`; null/Unicode cases in the server contract matrix; PostgreSQL continuation cases qualified in the M3 record |
| 8   | filters before taking a page                             | Edge     | many nonmatching rows precede valid matches      | eligible later entries remain reachable                         | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_filters_collection_before_pagination` |
| 9   | denies an unauthenticated browse                         | Error    | no valid session                                 | 401 without private entries                                     | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_denies_unauthenticated_library_browsing` |
| 10  | excludes inaccessible browse entries                     | Error    | caller lacks collection access                   | no restricted entries or leaked counts                          | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_excludes_unreadable_entries` |
| 11  | excludes trashed browse entries                          | Edge     | live and trashed candidates                      | only permitted live entries returned                            | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_excludes_trashed_collections` |
| 12  | rejects a cursor from another view                       | Error    | changed identity/filter/sort/page size           | typed rejection without mixed pages                             | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_rejects_rebound_cursor`; caller binding in `test_rejects_cursor_for_different_caller` |
| 13  | rejects a stale browse revision                          | Edge     | relevant mutation after first page               | refresh-required response                                       | Integration     | ✅ `integration/api/v1/models/test_browse.py::TestBrowseModels::test_requires_refresh_after_committed_write` |
| 14  | advances revision for each relevant writer               | Edge     | parameterized writer inventory                   | next continuation refuses the old revision                      | Integration     | ✅ `integration/db/test_library_contracts_v1.py::TestInstall::test_rejects_continuation_after_each_catalog_dependency_writer` |
| 15  | bounds browse database work at scale                     | Edge     | registered scale corpora                         | statement/parameter/time budgets satisfied                      | Integration     | ✅ `repo/test_read_scaling.py::TestLibraryReads::test_keeps_multipart_projection_bounded`; statement/bind sweeps and `repo/test_read_scale_budgets.py::TestLibraryReadsAtScale::test_answers_a_late_mixed_browse_page_within_budget` |
| 16  | keeps external list changes pending                      | Happy    | external ordering change                         | existing ordering retained with changes-available action        | Frontend unit   | ✅ `src/components/__tests__/model-grid.test.tsx::keeps displayed rows until explicit refresh` |
| 17  | requires refresh before continuing a changed list        | Edge     | change notice or stale cursor                    | no incompatible page appended                                   | Playwright      | ✅ `tests/e2e/vault.spec.ts::refreshes a changed library deliberately` |
| 18  | keeps continuation available after an empty page         | Edge     | zero displayed entries with next cursor          | Load more remains usable                                        | Frontend unit   | ✅ `src/components/__tests__/model-grid.test.tsx::reaches matching results after an empty browse page` |
| 19  | retains pages after continuation failure                 | Error    | next-page request fails                          | existing cards remain with retry                                | Frontend unit   | ✅ `src/components/__tests__/model-grid.test.tsx::retries a failed continuation without discarding read pages` |
| 20  | preserves confirmed favourite state across a late read   | Edge     | deferred refetch/page body after write           | list/detail show confirmed value                                | Frontend unit   | ✅ `src/features/library/__tests__/mutations.test.tsx::rejects a late page after a confirmed star` |
| 21  | removes a confirmed unstar from Favorites                | Happy    | server accepts unstar                            | card removed with reading position preserved                    | Playwright real | ✅ `tests/e2e-real/favorites.spec.ts::preserves the reading position after a confirmed favorite removal` |
| 22  | retains a favourite after rejected unstar                | Error    | server rejects action                            | card remains with recoverable error                             | Frontend unit   | ✅ `src/features/library/__tests__/mutations.test.tsx::keeps a favorite when unstar fails` |
| 23  | prevents an older edit from overwriting a newer edit     | Edge     | two concurrent writers use one base version      | one succeeds; other receives conflict                           | Integration     | ✅ `integration/modules/library/test_edit_preconditions.py::TestClaim::test_permits_only_one_atomic_editor` |
| 24  | preserves draft on edit conflict                         | Error    | stale edit version                               | unsaved text remains available beside latest authorized state   | Playwright real | ✅ `tests/e2e-real/documents.spec.ts::reviews a competing edit before saving the retained draft` |
| 25  | denies an edit after permission revocation               | Error    | edit permission revoked before save              | 403 and unchanged persisted user-editable state                 | Integration     | ✅ `integration/modules/library/test_edit_preconditions.py::TestClaim::test_rejects_revoked_actor` |
| 26  | does not replace dirty fields on background read         | Edge     | dirty form receives newer server response        | draft remains intact                                            | Frontend unit   | ✅ `src/components/model-detail/__tests__/index.test.tsx::preserves the editing base across a background refresh` |
| 27  | reconciles a lost save acknowledgement                   | Edge     | commit succeeds but response is lost             | explicit current-state recovery without blind overwrite         | Playwright real | ✅ `tests/e2e-real/documents.spec.ts::confirms a committed save after its acknowledgement is lost` |
| 28  | rejects a read from a previous session                   | Error    | session changes during headers or body           | old entity data never reaches the new session                   | Frontend unit   | ✅ `src/lib/api/__tests__/request.test.ts::rejects old-session response bodies for $label` |
| 29  | ignores a previous session mutation completion           | Error    | session changes before write acknowledgement     | new session state remains untouched                             | Frontend unit   | ✅ `src/lib/api/__tests__/request.test.ts::rejects retired mutation acknowledgements for $label` |
| 30  | ignores a previous session unauthorized response         | Error    | late old-session 401                             | new authenticated session remains active                        | Frontend unit   | ✅ `src/lib/api/__tests__/request.test.ts::ignores unauthorized bodies from retired sessions` |
| 31  | discards protected bytes from a previous session         | Error    | logout during blob/stream completion             | old bytes never displayed or retained for next identity         | Frontend unit   | ✅ `src/lib/api/__tests__/request.test.ts::rejects old-session response bodies for $label` |
| 32  | restores Back to the same reading position               | Happy    | paged list then detail then Back                 | same entry identity and nested scroll anchor                    | Playwright real | ✅ `tests/e2e-real/library-navigation.spec.ts::restores a paged Library reading position through real detail navigation` (desktop grid/mobile list) |
| 33  | restores separate history entries independently          | Edge     | same URL visited at different positions          | Back/Forward use the matching entry position                    | Playwright      | ✅ `tests/e2e/library-snapshot.spec.ts::keeps independent reading positions for repeated Library URLs` |
| 34  | falls back when the saved anchor is gone                 | Edge     | anchor deleted or cache unavailable              | bounded documented restoration without endless requests         | Playwright      | ✅ `tests/e2e/library-navigation.spec.ts::bounds history reconstruction when its anchor is removed` and `stale`; actual Query GC in all three cases |
| 35  | keeps a rapid navigation snapshot coherent               | Edge     | A to B to C with reordered responses             | heading/cards/actions belong to one displayed scope             | Playwright      | ✅ `tests/e2e/library-snapshot.spec.ts::keeps one coherent snapshot during rapid collection navigation` |
| 36  | hides a snapshot after permission loss                   | Error    | access invalidation while old list displayed     | restricted snapshot removed pending authorized revalidation     | Frontend unit   | ✅ `src/components/__tests__/model-grid.test.tsx::hides private content when authorization changes`; browser retirement in `tests/e2e/library-snapshot.spec.ts` |
| 37  | admits thumbnails near the viewport first                | Happy    | many offscreen cards                             | visible image requests precede distant ones under the limit     | Playwright      | ✅ [M6 validation](../frontend-m6-validation.md), M6 row1: protected-assets browser admission |
| 38  | retains a leased image during eviction                   | Edge     | idle-byte budget exceeded                        | mounted image remains usable                                    | Frontend unit   | ✅ [M6 validation](../frontend-m6-validation.md), M6 rows2–3: mounted lease and inactive-byte limit |
| 39  | releases abandoned thumbnail work                        | Edge     | queued consumer unmounts                         | unneeded request does not start                                 | Frontend unit   | ✅ [M6 validation](../frontend-m6-validation.md), M6 row4: abandoned queued image |
| 40  | publishes a completed outliner restoration batch         | Happy    | later batch still pending                        | completed branch usable immediately                             | Frontend unit   | ✅ [M6 validation](../frontend-m6-validation.md), M6 row5: independently completed restored branch |
| 41  | preserves range selection after page append              | Edge     | Shift selection across new page                  | expected Model identities selected                              | Playwright      | ✅ [M6 validation](../frontend-m6-validation.md), M6 row6: Shift after append browser regression |
| 42  | shares Inbox freshness across consumers                  | Happy    | badge and page observe same snapshot             | both reflect the same confirmed import state                    | Frontend unit   | ❌ missing |
| 43  | prevents a printer reconnect after disposal              | Edge     | cleanup before ticket or close callback          | no new authenticated socket is opened                           | Frontend unit   | ❌ missing |
| 44  | discards an obsolete event connection                    | Edge     | old connection resolves after a new subscription | no stale delivery to the active session                         | Frontend unit   | ❌ missing |
| 45  | recovers remote Job state after missed events            | Edge     | disconnect while Job progresses                  | authorized resync converges to persisted status                 | Playwright real | ❌ missing |
| 46  | preserves local transfer cancellation                    | Happy    | upload in progress                               | cancellation stops the local transfer with correct UI outcome   | Playwright real | ❌ missing |
| 47  | distinguishes document failure from empty success        | Error    | listDocuments fails                              | error/retry displayed instead of empty collection               | Frontend unit   | ❌ missing |
| 48  | preserves omitted provider credentials                   | Happy    | settings save omits secret                       | stored credential is retained                                   | Integration     | ❌ missing |
| 49  | keeps public share isolated from private navigation      | Error    | anonymous share visitor                          | private library data unavailable                                | Playwright real | ❌ missing |
| 50  | recovers from a stale route chunk                        | Error    | old shell requests removed asset                 | bounded recovery UI without reload loop                         | Playwright      | ❌ missing |
| 51  | recovers static navigation when Cache Storage fails      | Error    | cache API rejects                                | online route can still load                                     | Playwright      | ❌ missing |
| 52  | keeps private responses out of the service worker cache  | Error    | authenticated API and asset reads                | no private response available to next identity from shell cache | Playwright      | ❌ missing |
| 53  | preserves locale on initial navigation                   | Happy    | EN or ES preference                              | correct initial UI without wrong-locale flash                   | Playwright      | ❌ missing |
| 54  | keeps the extension capture contract compatible          | Happy    | supported provider capture                       | accepted pending import with correct authorization              | Contract        | ❌ missing |
| 55  | rejects forbidden module dependencies                    | Error    | fixture edge across an enforced boundary         | diagnostic names the offending edge                             | Frontend unit   | ❌ missing |
| 56  | upgrades the conditional-write schema with existing data | Edge     | prior supported schema with library rows         | data survives with valid initial edit versions                  | Integration     | ✅ `integration/db/test_library_contracts_v1.py::TestUpgrade::test_upgrades_existing_rows`; released-schema upgrade in the M3 closure record |

## Baseline and measurement protocol

Re-run on the implementation base before editing production code. Record commit,
working-tree provenance, resolved dependencies, machine/browser versions, backend,
database corpus and build identifier. Compare a new production build behind the
same proxy on spare ports; `:3000` is the prebuilt image. Use disposable data.

Include distributed root, dense mixed library, deeply nested remembered branches,
large result sets, empty results, two user permission sets, EN/ES, desktop/mobile,
warm reload and fresh browser contexts. Start with the supplied audit's sample
counts when comparing its scenarios (30 warm + 20 fresh per combination); report
all samples, median and p95 without silently dropping slow runs. A fresh browser
context is not a cold backend. Do not overlap profiling or test suites with timing.

Measure real content plus a successful first interaction, tree readiness, decoded
visible thumbnails and actual detail→Back scroll. Record network fanout/queue time,
API latency/queries, transferred bytes, JS execution, render commits and retained
blob memory separately. Image enumeration and skeleton presence are not readiness.
Use profiling builds separately and label their overhead. State exact numeric
budgets from the selected baseline before optimization; no invented improvement
percentage belongs in acceptance. Investigate regressions even if another metric
improves. First reduce contention/duplicate work before testing Virtual/persistence.

## Required checks for implementation

Follow `.agents/skills/printstash/references/testing.md`, its runtime references,
fixture rules, and `running-tests.md`. Use the repository's configured tools:

- Frontend: `pnpm format:check`, `pnpm lint`, `pnpm typecheck`; targeted Vitest
  files and affected workspace tests; real/browser specs for the actual flow;
  `pnpm build` for build/bundle changes. Full frontend/package suites run in PR CI.
- Backend contracts: focused integration cases, then fast/full as required;
  ruff and applicable pyright checks; generated OpenAPI review; SQLite upgrades
  and PostgreSQL cases for dialect-sensitive changes. New collection reads enter
  `_library_reads.py` and the scale checks; use repository factories.
- Extension: its configured lint/type/unit/build and capture-contract/browser lanes
  when affected; do not infer extension compatibility from web app tests.
- Import boundaries: repo tests for directions/cycles using the real alias/package
  resolver; no tests that merely assert new files exist.
- Coverage floors: Deep CI/release/floor tasks use `pnpm coverage` and backend
  coverage lane; focused tests cannot establish global floors. Playwright is
  invisible to coverage reports.

Security/data-integrity regression tests precede implementation. New capabilities
need a headline e2e flow. Do not lower floors, loosen fixtures, or add skips to
make migration tests pass.

## Documentation increment: actual validation

This increment changes Markdown and the review ledger only. Product format/lint/
type/unit/browser/build suites are not locally rerun to imply a refactor occurred.
Documentation validation: new local link targets resolved, all 762 ledger paths exist
without duplicates, all 57 matrix rows have the prescribed seven populated columns
and statuses (56 future behaviours plus D1 below), and the changed-file scope is
documentation only. The first oxfmt check identified formatting in six documents;
it was corrected before the final check. Required PR CI still must pass for the
latest commit under AGENTS.md; the PR records that actual result.

The existing ADR index links to missing `0007-model-families.md` on the base
commit. The link check reports that inherited gap separately; this plan adds no
new broken local link and does not reconstruct the missing decision.

| #   | Behaviour (test name)        | Category | Precondition / input    | Observable outcome asserted      | Tier          | Status                           |
| --- | ---------------------------- | -------- | ----------------------- | -------------------------------- | ------------- | -------------------------------- |
| D1  | changes production behaviour | Happy    | documentation-only diff | no production change to exercise | Frontend unit | ⏭️ N/A — planning artifacts only |

No correctness, maintainability-runtime or performance improvement is claimed.
The plan, accepted decisions and their validation requirements are the deliverable.

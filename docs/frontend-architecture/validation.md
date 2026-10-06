# Behaviour coverage and validation plan

Status: implementation qualification in progress. M2 rows 1–5 have reviewed
assertions and qualified executions, consolidated in the
[M2 closure record](../frontend-m2-closure-validation.md). Other rows remain the
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
| 6   | returns globally ordered mixed pages                     | Happy    | both entry kinds span pages                      | concatenated identities match canonical server order            | Integration     | ❌ missing |
| 7   | breaks equal sort values deterministically               | Edge     | equal values; null values; Unicode names         | stable order across repeated page reads                         | Integration     | ❌ missing |
| 8   | filters before taking a page                             | Edge     | many nonmatching rows precede valid matches      | eligible later entries remain reachable                         | Integration     | ❌ missing |
| 9   | denies an unauthenticated browse                         | Error    | no valid session                                 | 401 without private entries                                     | Integration     | ❌ missing |
| 10  | excludes inaccessible browse entries                     | Error    | caller lacks collection access                   | no restricted entries or leaked counts                          | Integration     | ❌ missing |
| 11  | excludes trashed browse entries                          | Edge     | live and trashed candidates                      | only permitted live entries returned                            | Integration     | ❌ missing |
| 12  | rejects a cursor from another view                       | Error    | changed identity/filter/sort/page size           | typed rejection without mixed pages                             | Integration     | ❌ missing |
| 13  | rejects a stale browse revision                          | Edge     | relevant mutation after first page               | refresh-required response                                       | Integration     | ❌ missing |
| 14  | advances revision for each relevant writer               | Edge     | parameterized writer inventory                   | next continuation refuses the old revision                      | Integration     | ❌ missing |
| 15  | bounds browse database work at scale                     | Edge     | registered scale corpora                         | statement/parameter/time budgets satisfied                      | Integration     | ❌ missing |
| 16  | keeps external list changes pending                      | Happy    | external ordering change                         | existing ordering retained with changes-available action        | Frontend unit   | ❌ missing |
| 17  | requires refresh before continuing a changed list        | Edge     | change notice or stale cursor                    | no incompatible page appended                                   | Playwright      | ❌ missing |
| 18  | keeps continuation available after an empty page         | Edge     | zero displayed entries with next cursor          | Load more remains usable                                        | Frontend unit   | ❌ missing |
| 19  | retains pages after continuation failure                 | Error    | next-page request fails                          | existing cards remain with retry                                | Frontend unit   | ❌ missing |
| 20  | preserves confirmed favourite state across a late read   | Edge     | deferred refetch/page body after write           | list/detail show confirmed value                                | Frontend unit   | ❌ missing |
| 21  | removes a confirmed unstar from Favorites                | Happy    | server accepts unstar                            | card removed with reading position preserved                    | Playwright real | ❌ missing |
| 22  | retains a favourite after rejected unstar                | Error    | server rejects action                            | card remains with recoverable error                             | Frontend unit   | ❌ missing |
| 23  | prevents an older edit from overwriting a newer edit     | Edge     | two concurrent writers use one base version      | one succeeds; other receives conflict                           | Integration     | ❌ missing |
| 24  | preserves draft on edit conflict                         | Error    | stale edit version                               | unsaved text remains available beside latest authorized state   | Playwright real | ❌ missing |
| 25  | denies an edit after permission revocation               | Error    | edit permission revoked before save              | 403 and unchanged persisted user-editable state                 | Integration     | ❌ missing |
| 26  | does not replace dirty fields on background read         | Edge     | dirty form receives newer server response        | draft remains intact                                            | Frontend unit   | ❌ missing |
| 27  | reconciles a lost save acknowledgement                   | Edge     | commit succeeds but response is lost             | explicit current-state recovery without blind overwrite         | Playwright real | ❌ missing |
| 28  | rejects a read from a previous session                   | Error    | session changes during headers or body           | old entity data never reaches the new session                   | Frontend unit   | ❌ missing |
| 29  | ignores a previous session mutation completion           | Error    | session changes before write acknowledgement     | new session state remains untouched                             | Frontend unit   | ❌ missing |
| 30  | ignores a previous session unauthorized response         | Error    | late old-session 401                             | new authenticated session remains active                        | Frontend unit   | ❌ missing |
| 31  | discards protected bytes from a previous session         | Error    | logout during blob/stream completion             | old bytes never displayed or retained for next identity         | Frontend unit   | ❌ missing |
| 32  | restores Back to the same reading position               | Happy    | paged list then detail then Back                 | same entry identity and nested scroll anchor                    | Playwright real | ❌ missing |
| 33  | restores separate history entries independently          | Edge     | same URL visited at different positions          | Back/Forward use the matching entry position                    | Playwright      | ❌ missing |
| 34  | falls back when the saved anchor is gone                 | Edge     | anchor deleted or cache unavailable              | bounded documented restoration without endless requests         | Playwright      | ❌ missing |
| 35  | keeps a rapid navigation snapshot coherent               | Edge     | A to B to C with reordered responses             | heading/cards/actions belong to one displayed scope             | Playwright      | ❌ missing |
| 36  | hides a snapshot after permission loss                   | Error    | access invalidation while old list displayed     | restricted snapshot removed pending authorized revalidation     | Frontend unit   | ❌ missing |
| 37  | admits thumbnails near the viewport first                | Happy    | many offscreen cards                             | visible image requests precede distant ones under the limit     | Playwright      | ❌ missing |
| 38  | retains a leased image during eviction                   | Edge     | idle-byte budget exceeded                        | mounted image remains usable                                    | Frontend unit   | ❌ missing |
| 39  | releases abandoned thumbnail work                        | Edge     | queued consumer unmounts                         | unneeded request does not start                                 | Frontend unit   | ❌ missing |
| 40  | publishes a completed outliner restoration batch         | Happy    | later batch still pending                        | completed branch usable immediately                             | Frontend unit   | ❌ missing |
| 41  | preserves range selection after page append              | Edge     | Shift selection across new page                  | expected Model identities selected                              | Playwright      | ❌ missing |
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
| 56  | upgrades the conditional-write schema with existing data | Edge     | prior supported schema with library rows         | data survives with valid initial edit versions                  | Integration     | ❌ missing |

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

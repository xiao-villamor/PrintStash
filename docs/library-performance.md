# Library performance acceptance

Target: authenticated production library on the reference host. First usable content and complete view are separate milestones. A complete view requires the current destination, restored branches, usable controls and decoded expected viewport images. Browser observation, using corpus identities, is independent of application marks. Missing derivatives are explicit placeholders; failed expected images fail acceptance.

Baseline: `8d70c5560958a3b1cdbd96773f8bf53a078736fe`. Work preserves the revision-aware `/models/browse` contract already merged on main. The older `/library/page` draft is superseded. Existing restoration work is ported selectively, preserving current edit tokens and access checks. Unrelated edits in the original checkout are excluded.

| Journey | Median (ms) | p95 (ms) |
|---|---:|---:|
| Cached reload | 500 | 800 |
| Fresh authenticated browser | 1000 | 1500 |
| Unprefetched collection | 400 | 800 |
| Cached return | 250 | 500 |
| Additional page | 300 | 500 |
| Local search including debounce | 650 | 1000 |

Each of distributed root, dense collection and deep selection with 27 remembered paths runs at 1440×900 and 390×844, in EN and ES: 30 reloads and 20 new authenticated browser contexts each, 600 samples per version. Servers are warm. No simulated API, dropped outliers or mixed cohorts. Profiling and test suites run separately.

## Reproduce

`pnpm perf:accept CONFIG.json OUTPUT_DIRECTORY` requires `PERF_USERNAME` and
`PERF_PASSWORD` for a real login. Credentials, cookies and authorization headers
are never written into reports. Use production assets and the real API behind
nginx. Record the exact commit, clean source state, image digest and corpus in
`identity`; a missing identity or dirty tree rejects acceptance.

The config supplies explicit expected destinations, ordered entry identities,
branch/leaf names and media availability (`available`, `pending`, `missing`).
The observer checks the destination and corpus before accepting images. It
calls `decode()` and observes painted frames independently of application marks.
Application marks are also mandatory in the after run. Pending derivatives are
reported separately from missing derivatives and failed expected downloads.

For disposable fixture servers, `tests.factories.library_startup` now supports
`distributed` and `rich` corpora and writes `corpus.json` inside its dedicated
fixture root. The rich corpus contains 20 roots plus seven nested collections,
93 Models with completed thumbnails and two Multipart Models. Generate expected
config with:

```sh
PERF_IMAGE=sha256:... node scripts/library-performance/config.mjs \
  /path/to/distributed/corpus.json /path/to/rich/corpus.json /path/to/config.json
pnpm perf:accept /path/to/config.json .startup-results/acceptance
```

`bash scripts/library-performance/start-backend.sh distributed` and the same
command with `rich` start isolated real APIs on ports 8520/8521. They retain
their own fixture roots under `.startup-results/library-acceptance/` and never
reset arbitrary directories. Serve production assets through nginx with those
upstreams on ports 3520/3521. The fixture login is `admin` / `admin1234`.
Never point a seeder at the real library. A real deployment needs its own
authorized corpus manifest.

The default command takes all 600 load samples plus 20 samples of each of the
four navigation journeys per device/locale (320 additional samples). Collection
navigation starts as soon as source content is published, without waiting for
source thumbnails. Back opens a Model and returns through history. Pagination
observes the enlarged ordered page; search includes the input and debounce.
No threshold override exists. Missing metrics, samples, cohorts or journeys
fail the command. `PERF_DIAGNOSTIC=1` permits shorter development runs, explicitly
labels their identity and never declares acceptance. Optional `PERF_SCENARIO`,
`PERF_DEVICE` and `PERF_LOCALE` select diagnostics; an incomplete acceptance run
still fails the cohort check.

## Measurement limitations discovered during the audit

The original deep-tree DOM observer found restored rows but did not verify that
their buttons had usable width. Seven nested levels could consume the entire
sidebar width. Historic deep-tree times therefore describe content restoration,
not proof of an entirely usable view. The browser regression test now actually
clicks the deepest collection on desktop and mobile. Preserve the historic
samples with this limitation rather than relabeling them as full acceptance.

First installation previously reloaded the document when the worker claimed it.
That repeated authentication and Library reads, and reset `performance.timeOrigin`.
The new observer preserves the navigation's absolute start across document
restarts. First installation now keeps the page; a later worker update still
reloads once. Earlier fresh-context timings do not include this stronger clock
contract and must not be presented as comparable final acceptance.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | waits for the restored tree before completing | Happy | Saved expansion, held restore | Completion follows restored leaf | Playwright | ✅ |
| 2 | waits for visible image decoding | Happy | Held decode | No complete mark before decode | Playwright | ✅ |
| 3 | rejects the previous navigation's late completion | Edge | Consecutive destinations | Old callback cannot settle successor | Frontend unit | ✅ |
| 4 | does not complete after restoration fails | Error | Restore 503 | Failed state without success | Playwright | ✅ |
| 5 | Restores 27 paths in bounded batches | Edge | 27 saved paths | Every path restored, maximum 16 per request | Frontend integration | ✅ |
| 6 | admits only visible thumbnails while critical downloads are pending | Edge | Offscreen cards | Only two visible transfers admitted | Playwright | ✅ |
| 7 | Rejects former-session restore responses | Error | Logout during read | No previous user cache entries | Frontend integration | ✅ |
| 8 | test_ignores_inaccessible_persisted_paths | Error | Partial permissions | No inaccessible rows | Backend integration | ✅ |
| 9 | test_preserves_mixed_order_across_continuation | Happy | Models and Multipart Models | No duplicates or omissions | E2E | ✅ |
| 10 | restores the nested grid reading position | Edge | Model then Back | Same anchor and offset on both devices | Playwright | ✅ |
| 11 | keeps one coherent snapshot during rapid collection navigation | Edge | Rapid destinations | Matching URL and final content | Playwright | ✅ |
| 12 | Meets host budgets | Happy | All reference-host cohorts | Every median and p95 inside defaults | Performance | ❌ final acceptance pending |
| 13 | does not complete after an expected thumbnail fails | Error | Image 503 | Failed state without success | Playwright | ✅ |
| 14 | restores the mobile tree before opening without declaring a hidden tree complete | Happy | Saved mobile expansion | Fetch before opening; completion after visible tree | Playwright | ✅ |
| 15 | keeps a deeply nested collection selectable | Edge | Seven nested levels | Label width exceeds 40px; click changes destination | Playwright | ✅ |
| 16 | test_continues_restored_folder_pages_with_their_original_cursor | Edge | Truncated folder page | Compatible cursor continues without omissions | Backend integration | ✅ |
| 17 | test_continues_restored_entry_pages_with_their_original_cursor | Edge | Truncated mixed entry page | Compatible cursor continues without duplicates | Backend integration | ✅ |
| 18 | test_restores_with_postgresql | Happy | Real PostgreSQL | Restoration and continuation preserve order | Backend integration | ✅ |
| 19 | retains slow outliers in p95 | Edge | Slow tail | Tail remains in percentile and fails budget | Repo | ✅ |
| 20 | rejects a missing metric | Error | Absent media time | Evaluator rejects sample | Repo | ✅ |
| 21 | rejects a missing application phase | Error | Incomplete marks | Evaluator rejects sample | Repo | ✅ |
| 22 | rejects an omitted device cohort | Error | Missing device/locale | Evaluator rejects run | Repo | ✅ |
| 23 | rejects an omitted journey | Error | Missing pagination samples | Evaluator rejects run | Repo | ✅ |
| 24 | preserves the first visit when the installed worker takes control | Happy | First worker claim | No document reload | Frontend unit | ✅ |
| 25 | reloads a later update after the first installation | Edge | Second worker claim | Exactly one reload | Frontend unit | ✅ |
| 26 | reloads once when replacing an installed worker | Happy | Old worker owns page | New worker triggers one reload | Production Playwright | ✅ |
| 27 | keeps the measurement clock across a document restart | Edge | Reload during startup | Elapsed time includes first document | Playwright | ✅ |
| 28 | test_allows_a_reader_of_the_parent_collection | Happy | Inherited collection permission | Thumbnail bytes returned | Backend integration | ✅ |
| 29 | test_denies_an_ungranted_thumbnail | Error | Missing collection permission | Thumbnail denied | Backend integration | ✅ |
| 30 | test_hides_trashed_thumbnail_owners | Error | File or Model trashed | Thumbnail hidden | Backend integration | ✅ |
| 31 | test_requires_authentication_for_restoration | Error | No session | 401 | Backend integration | ✅ |
| 32 | test_validates_restoration_bounds | Boundary | Empty/oversized paths or limit | Invalid input rejected | Backend integration | ✅ |
| 33 | test_restricts_printer_filters_during_restoration | Error | Member uses administrator filter | 403 | Backend integration | ✅ |
| 34 | test_rejects_incompatible_restoration_scope | Error | Cursor, parent or legacy scope | Invalid combination rejected | Backend integration | ✅ |
| 35 | test_ignores_trashed_persisted_paths | Edge | Remembered trashed path | No trashed content restored | Backend integration | ✅ |
| 36 | test_ignores_removed_persisted_paths | Edge | Remembered deleted path | Remaining branches restored | Backend integration | ✅ |
| 37 | test_preserves_granted_roots_during_restoration | Happy | Permission begins below hidden ancestor | Visible branch becomes root | Backend integration | ✅ |
| 38 | test_filters_restored_branches_before_paging | Happy | Scoped filters | Only matching entries count toward page | Backend integration | ✅ |
| 39 | test_applies_the_library_view_to_restored_entries | Happy | Mixed view modes | Correct Models / Multipart Models restored | Backend integration | ✅ |
| 40 | test_reveals_the_selected_folder_outside_a_restored_page | Edge | Selection beyond page | Reveal retains continuation cursor | Backend integration | ✅ |
| 41 | test_returns_the_restored_parent_label_for_entries | Happy | Nested parent | Correct display label without repeated projection | Backend integration | ✅ |
| 42 | seeds the tree before descendant hooks fetch | Happy | Batched restore | Child hooks reuse returned pages | Frontend integration | ✅ |
| 43 | reuses restoration after returning from a model | Edge | Selection changes | Cached pages survive | Frontend integration | ✅ |
| 44 | preserves downloaded cursor pages during restoration | Edge | Later pages already cached | Restoration does not overwrite them | Frontend integration | ✅ |
| 45 | isolates obsolete restoration responses | Error | Changed scope | Old response cannot populate current tree | Frontend integration | ✅ |
| 46 | falls back to paged reads when restoration fails | Error | Batch read fails | Ordinary read remains available for recovery | Frontend integration | ✅ |
| 47 | waits for sustained intent | Boundary | Hover shorter than 150ms | No speculative request | Frontend unit | ✅ |
| 48 | loads only the latest intended destination | Edge | Rapid pointer changes | Only latest target fetched | Frontend unit | ✅ |
| 49 | cancels abandoned intent | Edge | Pointer leaves | Pending timer/request retired | Frontend unit | ✅ |
| 50 | defers speculative reads during critical navigation | Edge | View incomplete | No speculative fetch | Frontend unit | ✅ |
| 51 | retires speculative work when navigation starts | Edge | Critical destination begins | Speculative work aborted | Frontend unit | ✅ |
| 52 | reports failed protected downloads explicitly | Error | Protected fetch fails | Failed thumbnail state | Frontend unit | ✅ |
| 53 | reports image element failures explicitly | Error | Browser image error | Failed thumbnail state | Frontend unit | ✅ |
| 54 | reports failed decoding explicitly | Error | Decoder rejects bytes | Failed thumbnail state | Frontend unit | ✅ |
| 55 | does not starve mobile controls while the tree is closed | Edge | Closed drawer after usable content | Secondary controls available without false completion | Frontend unit | ✅ |
| 56 | keeps a restored branch usable while a newly opened branch is pending | Edge | One slow branch | Other restored branch stays usable | Frontend integration | ✅ |
| 57 | loads a hovered folder description on navigation | Happy | Intent then selection | Description arrives on actual navigation | Frontend integration | ✅ |
| 58 | limits simultaneous protected image downloads to two | Boundary | Many image consumers | At most two active transfers | Frontend unit | ✅ |
| 59 | Library reads retain scale budgets | Scale | 25k collections / 100k Models | Bounded queries, parameters and wall time | Backend scale | ❌ pending |

The matrix remains open until final gates and deployment acceptance finish.

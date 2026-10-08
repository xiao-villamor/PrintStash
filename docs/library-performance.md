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

The `browser-reload-corpus-identities-v4` protocol uses an initial `goto` followed
by actual browser reloads. Every recorded warm sample must include exactly one
Navigation Timing entry with `type=reload`, including failed baseline samples.

Each of distributed root, dense collection and deep selection with 27 remembered paths runs at 1440×900 and 390×844, in EN and ES: 30 reloads and 20 new authenticated browser contexts each, 600 samples per version. Servers are warm. No simulated API, dropped outliers or mixed cohorts. Profiling and test suites run separately.

## Reproduce

`pnpm perf:accept CONFIG.json OUTPUT_DIRECTORY` requires `PERF_USERNAME` and
`PERF_PASSWORD` for a real login. Credentials, cookies and authorization headers
are never written into reports. Use production assets and the real API behind
nginx. Record the exact commit, clean source state, image digest and corpus in
`identity`; a missing identity or dirty tree rejects acceptance.

The config supplies explicit expected destinations, ordered entry identities,
branch/leaf paths with their display names and media availability (`available`, `pending`, `missing`).
Candidate acceptance requires unique `branchPaths` and `leafPaths` for every expected
row. DOM destinations must match these identities, so repeated labels cannot hide
an unrestored row. Old-image baselines explicitly retain label matching because
they predate the identity attributes. The observer checks folder and Model
identities together before accepting images, including mixed folder views. It
calls `decode()` and observes painted frames independently of application marks.
Application marks are also mandatory in the after run. New browser contexts
restore a session obtained through a real API login while starting with empty
browser caches; each document validates it through the real `/auth/me` endpoint.
Reports record hashes of the measurement scripts as well as the application image
and commit. Pending derivatives are
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

v4 also checks Models and media when a destination contains folders. The old
folder-only branch of the observer could complete a mixed view too early. Tree
rows now expose stable destinations; candidate acceptance uses these instead of
labels, which can repeat. Four browser regressions reproduced premature success
before these corrections. Historical v3 results retain their original observer
hash and must not be presented as v4 identity validation.

The v2 protocol used same-URL navigation for its `warm` series. Navigation Timing
records that operation as `navigate`, so those historical samples describe cached
document navigation, not the agreed browser reload. They remain retained with
that limitation and cannot establish the reload budget. v3 requires fresh matched
before/after reload cohorts; previous v2 results must not be relabeled or merged
into them. Fresh-context and in-app journey observations retain their original
meaning. No rejected historical run becomes accepted through this correction.


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

The navigation timer is armed before automation waits but starts at the browser's
actual click/input event. Back requires the Model route and its level-one heading;
a previous card title cannot stand in for an opened Model. Application marks must
belong to the measured navigation. Earlier navigation diagnostics that included
automation actionability waits or reused prior marks are not acceptance evidence.

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
| 59 | Library reads retain scale budgets | Scale | 25k collections / 100k Models | Bounded queries, parameters and wall time | Backend scale | ✅ local and CI budget checks, both roles; growth checks local |
| 60 | the independent observer waits for usable desktop/mobile library controls | Error | Hidden responsive duplicates, disabled search or mobile filter control | Only visible, enabled controls permit completion | Playwright | ✅ |
| 61 | mobile vault prepares bounded tree roots before opening the drawer | Happy | Mobile library with closed drawer | Bounded root read reused on opening | Playwright | ✅ |
| 62 | test_restores_unfiled_page | Edge | Library with/without a folder | Root count and unfiled identities agree | Backend integration | ✅ |
| 63 | preserves Models plus G-code after grouping deletion | Happy | Picker with more than one page | Selected Models and revisions survive deleting the set | Real-backend Playwright | ✅ |
| 64 | keeps a shared Model independently accessible | Edge | One Model used in two sets | Both references and independent navigation work | Real-backend Playwright | ✅ |
| 65 | moves a model reached through keyboard pagination beyond 500 entries | Edge | Late page, concurrent edit and duplicate display names | Exact seeded Model moved after conflict review and found in its destination | Real-backend Playwright | ✅ |
| 66 | starts interaction timing at the actual click/input | Edge | Automation waits before dispatch | Waiting is excluded; actual browser event starts a new observation | Playwright | ✅ |
| 67 | rejects phases from a previous navigation | Error | Earlier complete marks remain | Acceptance rejects stale phases | Repo | ✅ |
| 68 | test_rechecks_the_actor_after_a_previous_badge_read | Error | A prior reader or newly granted role | Each query applies current actor permissions | Backend integration | ✅ |
| 69 | keeps the events connection across a pending destination | Edge | Next page is loading | One connection, no inactive probe, fresh authority on arrival | Frontend unit | ✅ |
| 70 | selects committed navigation instead of preparation order | Edge | Concurrently prepared generation has a later start time | Only the committed, matching history entry supplies phases; inactive entries rejected | Playwright | ✅ |
| 71 | completes the committed mobile search destination | Happy | Search followed by opening the drawer | Current history destination has a completion mark | Playwright | ✅ |
| 72 | waits for the first presentation before connecting events | Edge | Initial page still pending | No event ticket competes with the first page; later pages retain their connection | Frontend unit | ✅ |
| 73 | publishes a decoded cached image without another load event | Happy | Cached source with pending decode | Ready after decode resolution without requiring another load | Frontend unit | ✅ |
| 74 | waits for load when early decoding cannot start yet | Edge | Decoder rejects before load starts | Pending until load, then decoded image becomes ready | Frontend unit | ✅ |
| 75 | opens only an immediately usable mobile tree control | Edge | Hidden, disabled or covered filter button | No click until visible enabled control passes hit testing | Playwright | ✅ |
| 76 | refreshes a changed library deliberately | Happy | Foreground event after library revision changes | Old page stays until explicit refresh; no stale continuation | Playwright | ✅ |
| 77 | checks permissions immediately while deferring the first events connection | Edge | Initial viewport unfinished | Authority checked immediately; one connection after readiness retained through later navigation | Frontend unit | ✅ |
| 78 | refetches an authorized empty child level after invalidation | Happy | Lookup confirms zero children, then collection changes | No serial empty read; invalidation retrieves new children | Frontend integration | ✅ |
| 79 | preserves already downloaded child pages when looking up a leaf | Edge | Child pages and cursors already cached | Existing pages remain intact | Frontend integration | ✅ |
| 80 | does not seed a leaf response that completes after cancellation | Error | Cancelled lookup resolves late | No cache data published | Frontend integration | ✅ |
| 81 | still reads children when the selected collection is not a leaf | Happy | Lookup reports children | Normal bounded child read publishes results | Frontend integration | ✅ |
| 82 | prioritizes the mobile tree until the filter section enters the viewport | Edge | Open drawer, temporary loading placeholder, offscreen catalogs, pending decoded media | No catalog reads until their controls enter the viewport, then immediate requests | Playwright | ✅ |

| 83 | prioritizes an explicitly focused filter while the mobile tree is pending | Edge | Pending roots, keyboard focus on a filter | Catalog requests start without waiting for tree or media completion | Playwright | ✅ |

| 84 | reveals a selected location beyond the first sibling page without walking previous pages | Edge | 65 siblings, last selected | Visible accessible selection without earlier page walks; continuation has no duplicate selection | Frontend integration | ✅ |

| 85 | measures a browser reload after the initial visit | Happy | One initial visit followed by a warm sample | Navigation Timing changes from navigate to reload; previous timing retired without losing preferences | Playwright | ✅ |
| 86 | rejects wrong reload evidence | Error | navigate/back_forward entries, including failed baseline | Acceptance rejects the sample before computing budgets | Repo | ✅ |
| 87 | rejects missing reload evidence | Error | No Navigation Timing entry | Acceptance fails instead of accepting an unproven reload | Repo | ✅ |

| 88 | rejects missing Models in a mixed folder view | Error | Expected folder present, expected Model absent | Observer stays incomplete until the expected Model is published | Playwright | ✅ `tests/e2e/library-readiness.spec.ts::rejects missing Models in a mixed folder view` |
| 89 | waits for decoded media in a mixed folder view | Edge | Folder and Model present, image download pending | Observer completes only after the expected image decodes | Playwright | ✅ `tests/e2e/library-readiness.spec.ts::waits for decoded media in a mixed folder view` |

| 90 | rejects a same-named branch with the wrong identity | Error | Matching label, wrong collection path | Observer waits for the exact corpus branch | Playwright | ✅ `tests/e2e/library-readiness.spec.ts::rejects a same-named branch with the wrong identity` |
| 91 | rejects a same-named leaf with the wrong identity | Error | Matching label, wrong Model path | Observer waits for the exact corpus entry | Playwright | ✅ `tests/e2e/library-readiness.spec.ts::rejects a same-named leaf with the wrong identity` |
| 92 | exposes restored row identities | Happy | Model and Multipart leaves under a restored branch | DOM identities match API destinations | Playwright | ✅ `tests/e2e/library-readiness.spec.ts::exposes restored row identities` |
| 93 | rejects incomplete branch identities | Error | Missing, truncated, duplicate or invalid branch paths | Acceptance rejects incomplete expectations | Repo | ✅ `tests/repo/library-performance.test.ts::rejects incomplete branch identities` |
| 94 | rejects incomplete leaf identities | Error | Missing, truncated, duplicate or invalid leaf paths | Acceptance rejects incomplete expectations | Repo | ✅ `tests/repo/library-performance.test.ts::rejects incomplete leaf identities` |
| 95 | accepts distinct paths with repeated labels | Happy | Different paths share display names | Valid corpus remains acceptable | Repo | ✅ `tests/repo/library-performance.test.ts::accepts distinct paths with repeated labels` |
| 96 | compresses detailed canonical previews | Happy | Legacy lossless WebP at 640 px | Output below 60% of input without changing dimensions | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_compresses_detailed_canonical_previews` |
| 97 | preserves thumbnail transparency exactly | Happy | Transparent preview with antialiased edges | Decoded alpha equals source alpha | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_preserves_thumbnail_transparency_exactly` |
| 98 | preserves visible thumbnail detail | Happy | Detailed color gradient preview | Visible RGB mean error below 4 levels | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_preserves_visible_thumbnail_detail` |
| 99 | preserves freshly rendered WebP bytes | Edge | Current renderer output explicitly identified by producer | No second lossy encode | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_canonical_renderer_webp_is_validated_without_reencoding` |
| 100 | normalizes mismatched renderer dimensions | Edge | Renderer image larger than configured width | Output obeys configured size despite trusted origin | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_normalizes_mismatched_renderer_dimensions` |
| 101 | rejects empty renderer output | Error | Transparent image marked as renderer output | Stable validation error before publication | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_rejects_empty_renderer_output` |
| 102 | compresses native rendered frames | Happy | Actual mesh rendered at 640 px | WebP smaller than equivalent lossless encoding | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_compresses_native_rendered_frames` |
| 103 | rejects invalid compression quality | Error | Out-of-range or boolean recipe quality | Profile load rejects invalid recipe | Unit | ✅ `packages/printstash-core/tests/mesh/test_preview_profile.py::TestPreviewProfile::test_profile_structure_rejects_invalid_recipe` |
| 104 | serves compressed previews after processing resumes | Happy | Upload while disabled, then enable processing | API serves compressed WebP at configured resolution | E2E | ✅ `e2e/test_derivative_controls.py::TestDerivativeControls::test_disable_upload_enable_produces_a_preview` |
| 105 | backfills previous thumbnail recipes | Edge | Ready metadata with previous mesh/G-code thumbnail recipe | Artifact becomes eligible for derivative Job | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_backfills_previous_thumbnail_recipes` |
| 106 | preserves decoded alpha in every output format | Edge | PNG/WebP with opaque, transparent and partial alpha pixels | Exact decoded alpha values | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_decoded_alpha` |
| 107 | freezes compressed visual inputs by recipe | Edge | Current preview profile and known tetrahedron | New thumbnail hash and recipe identity, unchanged six view hashes | Integration | ✅ `integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes` |
| 108 | diagnostic CPU views match production inputs | Happy | Same mesh through CPU diagnostic | Same seven frozen input hashes | Integration | ✅ `integration/scripts/test_gpu_render_measurement.py::TestMeasure::test_preserves_frozen_cpu_multiview_inputs` |
| 109 | forced regeneration publishes the replacement preview | Happy | Existing thumbnail replaced with a distinct color | Served center matches replacement within three RGB levels | Integration | ✅ `integration/api/v1/ingest/test_ingest_api.py::TestIngestModel::test_force_rebuild_refreshes_existing_mesh_thumbnail` |
| 110 | embedded preview survives unsupported geometry | Error | Unsupported 3MF with usable embedded preview | Correct preview color within three levels; source download identical | E2E | ✅ `e2e/test_ingest.py::TestThreeMFCapabilities::test_required_extension_refusal_preserves_independent_artifacts` |
| 111 | benchmark persists the renderer output once | Happy | Real full renderer with compact WebP | Persisted bytes match render phase output size | Integration | ✅ `integration/scripts/test_bench_thumbnails.py::TestMain::test_serializes_real_phase_evidence` |
| 112 | upload publishes the embedded preview | Happy | 3MF with a colored preview | Served preview retains size, transparency and color within three levels | E2E | ✅ `e2e/test_ingest.py::TestMetadata::test_3mf_upload_persists_embedded_preview` |
| 113 | saves search preferences beside another SQLite writer | Error | New/existing preferences; unrelated writer commits after lookup | Preferences persist without HTTP-500-causing stale snapshot | Integration | ✅ `integration/modules/search/test_preferences.py::TestUpdate::test_saves_preferences_beside_another_sqlite_writer` |
| 114 | retains stages after thumbnail encoding failure | Error | Codec raises after rendering | Diagnostic retains stages and the original encoding error | Unit | ✅ `unit/scripts/test_bench_thumbnails.py::TestBenchmarkFile::test_retains_stages_after_encoding_failure` |
| 115 | persists editable filters without repeated parsing | Happy | Real authenticated browser saves search consent | Successful save and editable parsed filters | Real-backend Playwright | ✅ `tests/e2e-real/ai-search/nl-filters.spec.ts::persists editable filters without repeated parsing` |
| 116 | backfill progresses during foreground arrivals | Edge | Real DBOS/native mesh processing under coverage | Both backfills and queued foreground work finish inside the unchanged deadline | E2E | ✅ `e2e/test_ingestion_fairness.py::TestIngestionFairness::test_backfill_progresses_during_sustained_interactive_arrivals` |
| 117 | canonical profile uses bounded encoder effort | Happy | Shared preview profile | Quality 90 and method 4 pinned by the public profile contract | Unit | ✅ `packages/printstash-core/tests/mesh/test_preview_profile.py::TestPreviewProfile::test_canonical_thumbnail_profile_is_stable` |
| 118 | completes full-capacity backfill | Edge | Real native processing with foreground arrivals | Full-capacity backfill finishes inside the unchanged deadline | E2E | ✅ `e2e/test_ingestion_fairness.py::TestIngestionFairness::test_full_capacity_backfill_eventually_completes` |

The matrix remains open until final gates and deployment acceptance finish.

The application publishes the currently committed navigation separately from its
preparation time. Concurrent React work can prepare generations out of commit
order; accepting the last start mark alone was incorrect. The acceptance reader
also requires its history key to match the browser's current entry. Thumbnail
readiness reuses the decoded state owned by `ProtectedThumbnail`, avoiding a
second per-image decode pass across frames; the independent browser observer
still calls `decode()` itself.

Mobile acceptance opens the tree at the first frame where its button is enabled,
visible and passes hit testing. This models the agreed immediate second gesture
without adding driver actionability waits. It does not shorten drawer motion or
change the independent full-view checks. The before and after cohorts must both
use this protocol; older mobile samples that used Playwright click waits remain
separate and must not be mixed into the comparison.

An authorized lookup that reports zero child collections seeds the ordinary empty
child-page Query entry. It does not replace already downloaded pages or seed a
cancelled response; normal invalidation still refetches that entry. This removes
one serial round trip when opening a leaf collection. The first event connection
waits for secondary reads to be admitted, while authority checks remain immediate;
once connected, it remains open across pending destinations.

Opening the mobile drawer admits the tree first. Catalogs below the viewport are
requested when their filter region becomes visible in the restored tree layout,
or after full-view readiness. A loading placeholder cannot prematurely admit
catalogs. Focusing or pressing a filter and the desktop filter toggle still express
immediate intent. This preserves useful
filter interactions without competing with restoration for offscreen data.

A separate codec diagnostic on 91 authorized thumbnails compared WebP qualities
90/85/80 at 640 px and quality 85 at 480/320 px. Quality 90 with method 6 at the existing
640×480 resolution reduced total bytes from 4,603,434 to 1,254,838 (72.7%);
median size fell from 43,332 to 12,624 bytes. All 91 alpha planes remained
byte-identical; alpha-weighted visible RGB PSNR was 43.41 dB median and 34.30 dB
minimum. Representative detailed, largest and worst-PSNR images were inspected
visually. These are codec diagnostics, not browser acceptance times; source
images and item identities stay private. The final deployment must regenerate
through normal derivative Jobs before measuring its actual served outputs.

Expanded CI exposed a search-preference save returning HTTP 500. A separate real
SQLite regression reproduces a stale read-to-write snapshot when background work
commits between the preference lookup and its update. Preference writes now reserve
the SQLite writer before reading, using the existing transaction helper. Both first
save and existing preferences pass the two-connection test; the authenticated
browser consent/filter flow also passes. PostgreSQL keeps its existing row lock.

A subsequent encoder-effort diagnostic selected method 4. Across the same 91
original previews it produced 1,284,894 bytes (72.1% below the lossless baseline),
with a 12,860-byte median and 39,256-byte maximum. Median encoding took 70.7 ms,
compared with 1,123.7 ms for method 6 in the earlier codec run. The three paired
probe images took 25–40 ms versus 1,279–1,511 ms. Alpha was exact in all 91 images;
visible PSNR was 43.58 dB median and 34.32 dB minimum. These remain diagnostics,
separate from acceptance. Method 4 avoids spending much more CPU for method 6's
additional 2.4% byte reduction.

The native fairness regression under backend coverage passed both cases in 73.95 s
with method 4. The existing 110 s fixture deadline was unchanged. Focused core
checks passed 218 cases and backend compression/fixture checks passed 28 cases.

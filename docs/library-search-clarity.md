# Library search and browsing clarity

The library keeps typing and Enter in the current view. Enter flushes the query,
closes suggestions and preserves collection, tags and other filters. The explicit
**Search with AI** suggestion opens the existing search route; Enter on that route
submits another AI query. Result cards retain navigation, previews and subject
labels while diagnostic evidence remains available only in the API.

Collection navigation stays visible. Advanced filters open on demand, with active
sections expanded for shared URLs and saved views. Removable filter chips remain
above results. Library tools contains organization commands; active selection has
its own count and Done action. Detail tabs use a two-row grid, and Similar cards
switch layout according to their panel width.

## Retrieval policy

Name recovery is a separate lexical signal: a single normalized token of at least
five characters can differ by one insertion, deletion, substitution or adjacent
transposition. Indexed vocabulary prefix probes and authorized posting queries
have fixed candidate caps. Exact names precede recovered spelling matches.
Candidate retrieval, final materialization and cursor context preserve visibility,
structured filters and live/trashed restrictions. Leading-character recovery uses
Latin letters, digits and characters in the query; it is not a complete Unicode
spellchecker.

A small domain vocabulary finds functional metadata through indexed conjunctive
phrases (for example a holder's cradle or a gear's toothed wheel). This does not
supply visual labels or replace the embedding models. Semantic native scores must
clear their own threshold before rank fusion; multiple weak legs cannot bypass
that threshold. Administrator per-space overrides remain authoritative.

The calibrated policy applies to single-word text queries using the pinned BGE
small English encoder and CLIP B/32 thumbnail encoder. Text uses 0.67 and thumbnail
CLIP uses 0.27. Other models, multiword queries, multiview, point-cloud, uploaded
images and related-Model queries retain their existing independently measured
policies. Index identities, public API contracts and visual aggregation are
unchanged. These bounded fixtures do not establish universal relevance across
arbitrary libraries or languages.

## Reproducing quality measurements

`backend/tests/fixtures/search/clarity-corpus.json` freezes four calibration objects,
four absent calibration concepts, seven held-out objects and six validation
queries. Geometry is original or repository-owned analytic geometry. The goose has
an opaque name and no description; the holder's description expresses function
without the word “holder”. Distractors include misleading filenames. No private
library geometry, descriptions or screenshots are fixtures.

`clarity-vectors.json` contains real measured CPU embeddings, model revisions,
render hashes, corpus/generator hashes and inference timings. Regenerate explicitly
with the pinned local model exports:

```sh
cd backend
.venv/bin/python -m tests.fakes.search_clarity_measure \
  --bge-dir /path/to/pinned-bge --clip-dir /path/to/pinned-clip \
  --output tests/fixtures/search/clarity-vectors.json
.venv/bin/python -m tests.fakes.search_clarity_calibrate
.venv/bin/pytest tests/integration/modules/search/retrieval/test_clarity_quality.py -q -s
```

Calibration uses only calibration rows: take the maximum unrelated cosine,
including cross-object and absent-concept negatives, add 0.01 and round upward to
0.01. Text's maximum is 0.65416; thumbnail CLIP's is 0.25917. Native positive recall
on calibration objects is respectively 1/4 and 3/4; functional lexical retrieval
recovers gear and bolt without accepting their weak semantic similarities. The
held-out assertions do not choose these thresholds.

The existing text benchmark and visual benchmark remain unchanged. Visual quality
covers thumbnail and multiview styles, point-cloud retrieval and an attributed
photograph of a physical Benchy. Uploaded-image and related-Model behavior is
verified separately from text-to-appearance retrieval.

The baseline replays the retrieval implementation from `4b9afeb9` against the same
frozen database and measured vectors. All six new acceptance assertions fail at
that baseline; all pass with the calibrated implementation.

| Metric | Base | Implementation |
|---|---:|---:|
| Positive recall@5 (4 queries) | 4/4 | 4/4 |
| Precision among returned top-five results | 4/20 | 4/4 |
| Absent-concept abstention | 0/2 | 2/2 |
| Relevant Spectre rank (`specter`) | 1 | 1 |
| Relevant holder rank | 3 | 1 |
| Median positive-query replay wall time | 640 ms | 1,140 ms |

Wall times are single observations under shared-host CPU contention, replaying
measured embeddings rather than running inference. They establish neither a speed
improvement nor a stable performance regression. The committed vector file retains
separate actual CPU-inference timings. Exact spelling recovery also passes with AI
disabled, so finding Spectre no longer depends on a semantic match.

Verification evidence includes 37 lexical/relevance integration tests, both new
visibility cases, and both uploaded-image API cases (strong and below-floor).
Core coverage ran 2,105 tests and all five floor checks: 99.28% combined
statement/branch coverage; both new helpers have 100% coverage. New PostgreSQL
cases passed. The rollback regression now explicitly advances the base branch's
deferred projection worker, restoring its intended publication assertion.
Existing text, visual and sparse quality files passed.

On commit `7279d1d2`, CI passed all 2,712 frontend tests (2,453 app, 199 UI,
60 domain), lint, type checking and the ratcheted coverage gate. App coverage was
82.45% statements and 77.64% branches. The previously interrupted local undo run
is superseded by that complete clean-runner verification. All 81 mock-browser
tests passed, including the five new search/detail cases. Missing search-status
and caption mocks and an obsolete Spanish accessible-label locator are corrected.
The real-browser helper waits for the toolbar before choosing its responsive menu, and the Similar saved-view workflow opens the advanced filters and Library
tools explicitly. Local real-backend runs verified the AI-search headline flow,
collection creation/deletion, saved views, Favorites, multipart creation, all three
Family flows, Similar review, batch tag/delete, selection moves with Undo, revision labels and
permission preflight. The required WebDAV restart/safe-GC flow also passed after
correcting its stale heading assertion to the base branch’s “Storage location”
label. The remote-backup suite also opens the existing migration disclosure before
using its shared provider picker; all three backup/Nextcloud browser cases passed
locally, including linked-target protection. The migration browser flow opens that
disclosure before planning and again after restart, preserving all copy, cutover,
audit and downloaded-byte assertions. Both onboarding variants passed (four cases),
and the migration restart/cutover case passed. Final CI status is tracked on PR #178.
The natural-language browser flow opens Search options before preferences and saved
views, and verifies that restored filters do not trigger another parser call.
The point-cloud flow checks retained `point_cloud` API evidence while asserting that
the removed explanation and shape-match text are absent from result cards. Both
updated flows passed locally. A separate visual-flow retry recorded
`ERR_NETWORK_CHANGED`; the same flow passed in the preceding run, and its assertions
and timeouts remain unchanged.

Manual inspection used only repository mock data at 390px and 1280px, both themes,
and the 400px minimum detail panel. Library and search pages had no horizontal
overflow. Enter retained the library route; the labeled AI action opened results.
The Similar panel showed readable names and a reachable Compare action. Automated
browser artifacts include phone/desktop screenshots, Back and clear-query checks,
and detail-tab keyboard focus assertions.

Security diff scans of `4b9afeb9..b91ab3ef` and `b91ab3ef..da83f8d9` found no
reportable findings across all production inventory entries. Incremental scans
cover the subsequent retrieval, CI configuration and regression changes. The final
caption transaction change receives its own immutable incremental review before publication.
Impeccable's mechanical detector reported no findings in the changed components.
Python 3.13 reproduced an anonymous SQL CTE name collision in the unchanged sparse
quality benchmark. A materialized keyword-score CTE now has an explicit nested
name, preventing reuse of its original temporary object's identity. Ranking and
authorization predicates are unchanged. The frozen benchmark and all ten sparse
integration cases passed on Python 3.13, including repeated filtered queries and
two independently composed readers. All 49 focused lexical, sparse and clarity
retrieval cases subsequently passed together, followed by three successful Python
3.13 benchmark replays with different hash seeds. All 14 PostgreSQL search
cases passed, including sparse expansion and visibility. The library empty-state browser fixture now
uses a unique single token: its former hyphenated phrase contained “model” and
correctly matched Models created by earlier scenarios.
Compatibility testing also exposed fixture timing assumptions. Warmup cancellation
now uses file-backed SQLite WAL, matching production concurrency. The thumbnail
fallback test now asserts the base branch's distinct Rust/multiview and media
thumbnail render identities, including that incompatible vectors are not copied. The isolated
consumer waits for search publication because the live worker may already hold the
projection lease. The consumer and cancellation assertions remain unchanged; the obsolete thumbnail
reuse assertion now verifies the base branch’s explicit recipe separation.

The scale-test fixture also retires/drops its native index DDL before its registry
rows are cleared. The leak was reproduced by running scale tests immediately
before the unchanged Alembic schema-drift checks.

The Python 3.13 compatibility run passed all 13,037 ordinary cases, then exhausted
its old 30-minute job limit while progressing through the serial provider-contract
pass. Its allowance is now 60 minutes, with the full test command unchanged and a
workflow regression check. The required backend run passed 13,037 ordinary cases
and 286 provider cases; its coverage audit then identified six inherited preview
and visual-index boundary gaps. Focused regression tests cover those paths without
lowering any floor. Commit `7279d1d2` subsequently passed all 13,052 ordinary
cases, 286 provider contracts and ten coverage checks at 94.14% combined coverage.
The new unit boundaries passed 25 cases; seven focused
integration cases passed after correcting their fixtures. All 157 cases in the
six affected test files subsequently passed together. Broader backend and real-browser gates must finish
successfully before this PR is marked ready; interrupted runs are not passed gates.

The compatibility workflow edit also activated the migration smoke test. Docker
Hub denied its pinned MinIO pull; [MinIO’s documented Quay registry](https://github.com/minio/minio/blob/master/docs/docker/README.md)
serves the identical release and manifest digest. Only the registry address changes;
the release, digest, network isolation and retained source volume are unchanged.

Caption dismissal exposed a real SQLite WAL read-to-write upgrade race. A two-connection
regression reproduced the failure before the fix; reserving the writer before reading
keeps caption changes writable while preserving the existing transaction helper contract. All
76 focused caption/API/session cases passed, followed by the real caption browser
flow. A separate rollback regression verifies that an existing caller transaction
retains ownership. Storage restart tests stop browser polling during their deliberate
offline window while retaining the same credentials and all byte-integrity assertions.
WebDAV and authenticated S3 delivery passed; optional Nextcloud/SFTP cases were not
configured in that local run.

## Acceptance coverage

Status denotes final verified evidence, not merely a test's presence.

| # | Behavior | Category | Input | Asserted outcome | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Enter preserves library filtering | Happy | Query plus active filters | Library URL retains filters | Frontend unit | ✅ CI unit/coverage |
| 2 | Explicit AI action opens results | Happy | Ready AI, nonempty query | Dedicated search route | Frontend unit | ✅ CI unit/coverage |
| 3 | Clearing preserves other filters | Edge | Filtered library | Only query removed | Frontend unit/browser | ✅ CI unit and mock-browser |
| 4 | Late responses stay stale | Edge | Replaced/cleared query | No stale suggestions | Frontend unit | ✅ CI unit/coverage |
| 5 | Cards omit explanations | Happy | Evidence-bearing results | No explanation disclosure | Frontend unit | ✅ CI unit/coverage |
| 6 | Advanced filters start collapsed | Happy | Default library | Collection navigation visible | Frontend unit | ✅ CI unit/coverage |
| 7 | Active filters are discoverable | Edge | Shared URL/saved view | Expanded sections and chips | Frontend unit/browser | ✅ CI unit and mock-browser |
| 8 | Clear all resets advanced filters | Edge | Family and ordinary filters | Default filtering, preserved sort | Frontend unit | ✅ CI unit/coverage |
| 9 | Secondary actions retain permissions | Error | Read-only collection | Restricted actions disabled | Frontend unit | ✅ CI unit/coverage |
| 10 | Tabs fit narrow panels | Edge | 400px detail panel | No horizontal overflow | Playwright | ✅ CI narrow-panel browser |
| 11 | Similar names remain readable | Edge | Narrow desktop panel | Name width and reachable Compare | Playwright | ✅ CI narrow-panel browser |
| 12 | Specter finds Spectre | Happy | Spelling variants/distractors | Intended result first | Backend integration | ✅ 37-test final retrieval run |
| 13 | Goose matches appearance | Happy | Opaque name, original geometry | Relevant result in top five | Backend integration | ✅ 37-test final retrieval run |
| 14 | Holder matches function | Happy | Indirect name, functional metadata | Relevant result in top five | Backend integration | ✅ 37-test final retrieval run |
| 15 | Weak matches are rejected | Edge | Absent concept / below-floor image | No strong matches, AI remains available | Backend integration | ✅ Measured text plus image API contract |
| 16 | Search respects visibility | Error | Private/trashed/filtered candidates | No unauthorized results | Backend integration/PostgreSQL | ✅ Both candidate paths and PostgreSQL cases |
| 17 | Semantic failure preserves keywords | Error | Inference failure | Keyword results, accurate status | Backend integration | ✅ 46-case schema/retrieval run |
| 18 | Revised search works end to end | Happy | Real backend/local index | Library → explicit AI results | Real-backend Playwright | ✅ Real local-index headline flow |
| 19 | Selection always offers Done | Edge | Grouped Family view, keyboard selection | Count and Done visible outside tools | Frontend unit | ✅ CI unit/coverage |
| 20 | Localized search shortcut | Happy | Spanish UI, slash key | Search library receives focus | Playwright | ✅ Six focused browser cases |
| 21 | Independent consumer sees deferred publication | Edge | Background projection already leased | All four searchable subject types returned | Backend e2e | ✅ Isolated consumer cases |
| 22 | Revoked local consent cancels warmup | Edge | Concurrent WAL read/write | Loader stops; provider is not warm | Backend integration | ✅ Focused compatibility regressions |
| 23 | Thumbnail fallback preserves render identity | Edge | Rust multiview / media thumbnail versions differ | No incompatible copying; fallback still serves search | Backend integration | ✅ Focused compatibility regressions |
| 24 | Native scale fixtures leave no schema drift | Edge | Scale tests followed by Alembic comparison | No leaked native tables; unmanaged drift still detected | Backend repo/integration | ✅ 46-case schema/retrieval run |
| 25 | WebDAV configuration survives restart | Happy | Setup, restart, safe-GC preview | Active provider and retained remote bytes verified | Real-backend Playwright | ✅ Full restart/lifecycle flow |
| 26 | Storage cleanup requires confirmation | Happy | Receipt-verified expired staging fixture | Bytes remain before confirmation and disappear afterward | Real-backend Playwright | ✅ Real file lifecycle verified |
| 27 | Compatibility CI includes provider contracts | Edge | Full lane follows ordinary tests with serial provider tests | Sufficient bounded job allowance, full command retained | Backend repo | ✅ 17 workflow checks |
| 28 | Empty geometry has no thumbnail | Edge | Missing mesh | No fabricated preview | Backend unit | ✅ Focused boundary tests |
| 29 | Native render failure is recoverable | Error | Renderer exception | Missing derivative rather than API failure | Backend unit | ✅ Focused boundary tests |
| 30 | Hostile ASCII yields only valid geometry | Error | Malformed vertices and oversized lines | Valid bounded sample marked incomplete | Backend unit | ✅ Focused boundary tests |
| 31 | Invalid thumbnail width is rejected | Error | Out-of-range or noninteger width | Stable validation failure | Backend unit | ✅ Focused boundary tests |
| 32 | Worker publishes complete output | Happy | Real native STL render | Complete manifest and atomic output | Backend unit | ✅ Focused boundary tests |
| 33 | Late thumbnail failure preserves current owner | Edge | Lease replaced during failed render | New lease unchanged; no publication | Backend integration | ✅ Seven focused integration cases |
| 34 | Thumbnail vectors survive rebuilding | Edge | New generation with the same thumbnail recipe | Verified bytes reused without rendering | Backend integration | ✅ Seven focused integration cases |
| 35 | Native thumbnail decoder failure is recoverable | Error | Decoder exception | No raw untrusted preview | Backend unit | ✅ Focused boundary tests |
| 36 | Invalid embedded base64 is rejected | Error | Malformed G-code thumbnail payload | No embedded preview | Backend unit | ✅ Focused boundary tests |
| 37 | Truncated thumbnail line is rejected | Edge | Unterminated G-code header | No embedded preview | Backend unit | ✅ Focused boundary tests |
| 38 | Legacy migration can pull its pinned source | Error | Docker Hub image unavailable | Identical Quay digest; twice-verified copy retains source objects | Repo / container integration | ✅ 22 config cases and two real migrations |
| 39 | Shared provider forms preserve linked targets | Edge | Nextcloud connection edited through current storage navigation | Credentials retained; incompatible linked-target edit rejected | Real-backend Playwright | ✅ All three remote-backup cases |
| 40 | Repeated sparse queries compile safely | Edge | Changed queries and independently composed readers on Python 3.13 | Stable ranking; excluded passages remain absent | Backend integration | ✅ Eleven sparse cases including the frozen benchmark |
| 41 | Unmatched library search has an empty state | Edge | Populated library and unique absent token | Empty-state message and no Model links | Real-backend Playwright | ✅ Seeded browser regression |
| 42 | Migration controls remain reachable after restart | Edge | Collapsed storage section before planning and recovery | Verified cutover, audit and unchanged Artifact bytes | Real-backend Playwright | ✅ Complete migration lifecycle |
| 43 | Natural-language controls remain discoverable | Edge | Search options opened before preferences and saved views | Editable filters restore without another parser call | Real-backend Playwright | ✅ Complete natural-language flow |
| 44 | Point-cloud evidence remains available through the API | Happy | Ready local point profile and matching query | API evidence retained; result-card explanations absent | Real-backend Playwright | ✅ Complete point-profile flow |
| 45 | Caption dismissal survives a concurrent writer | Edge | Two real SQLite WAL connections at caption lookup | Dismissal persists without changing human description | Backend integration / real-backend Playwright | ✅ `integration/modules/search/captions/test_concurrency.py::TestPatch::test_dismissal_survives_an_unrelated_writer_after_caption_lookup`; caption browser flow |
| 46 | Writer reservation respects caller rollback | Edge | Existing transaction contains uncommitted Model edit | The pending Model edit rolls back | Backend integration | ✅ `integration/db/test_transactions.py::TestBeginWrite::test_writer_reservation_preserves_an_existing_transaction` |

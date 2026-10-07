# Conditional Library movement — M4

M0–M3 are locally closed. M4 remains active; later milestones are not started.

The single-model drop handler still calls `updateModel` without a version. It is
shared by native grid/list dragging and the paginated sidebar. A lookup in the
visible grid is insufficient: the sidebar can move a Model beyond the grid's
loaded pages. Its current DTO has no editing base, and native drag data carries
only an id. Fetching a fresh version just before writing would authorize a stale
intent rather than detect a conflict.

First expose the existing aggregate version in lightweight Model/Multipart
outliner entries and search results. Collection search matches remain a distinct
unversioned case. Versions come from the same SQL row as the displayed name and
location; no per-entry hydration, new storage, migration or new cache is needed.
Then carry the captured id/version through both drag systems, enforce the version
at the shared command boundary and remove optional-version `updateModel` calls.
Keep the intended destination on conflict/uncertain results and require explicit
review before any retry. Preserve session and navigation lifetime fences.

## Read-contract coverage, before implementation

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | returns the Model editing base in outliner entries | Happy | Model version7 in folder/root | Exact id/name/location/version in authorized row | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_returns_the_aggregate_editing_base` |
| B2 | returns the Multipart editing base in outliner entries | Happy | Multipart version7 in folder/root | Exact aggregate version, independent of Model identity | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_returns_the_aggregate_editing_base` |
| B3 | preserves versions across mixed outliner continuation | Edge | Model/Multipart same numeric id, limit1 | Each kind keeps its own version across pages | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_preserves_versions_across_mixed_continuation` |
| B4 | distinguishes unversioned collection search matches | Edge | Search matches Model/Multipart/Collection | Aggregate versions present; Collection has no edit_version | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_distinguishes_unversioned_collection_search_matches` |
| B5 | reads the version acknowledged by a concurrent editor | Edge | Conditional Model PATCH after old outliner read | Next outliner read returns new name/location/version together | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_reads_the_version_acknowledged_by_another_editor` |
| B6 | returns editing versions from the legacy outliner projection | Happy | Existing /models/outliner caller | Same stored Model version | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_returns_editing_versions_from_the_legacy_outliner` |
| B7 | requires aggregate versions in the public read schema | Error | OpenAPI read schemas | Model/Multipart versions required; Collection match unversioned | Integration | ✅ `backend/tests/integration/api/v1/test_outliner.py::TestOutlinerEditingBase::test_requires_aggregate_versions_in_the_public_read_schema` |
| B8 | preserves versioned mixed search on PostgreSQL | Edge | Real PostgreSQL UNION includes Collection null slot | Exact per-kind versions; Collection shape unchanged | Integration | ✅ `backend/tests/integration/postgres/test_collection_tree.py::TestOutlinerOnPostgres::test_preserves_versioned_mixed_search` |
| B9 | keeps outliner read work bounded | Edge | Registered entries/search scale checks | Existing statement/parameter budgets pass | Integration | ✅ `backend/tests/repo/test_read_scaling.py::TestLibraryReads::test_runs_as_many_statements_at_ten_times_the_size / test_binds_as_many_parameters_at_ten_times_the_size (outliner cases)` |
| B10 | builds a minimal versioned outliner fixture | Happy | Shared builder with defaults/custom version | Complete lightweight DTO without rich Model fields; chosen version preserved | Frontend unit | ✅ `frontend/src/test-support/__tests__/factories.test.ts::aOutlinerModel::builds a minimal outliner fixture at version %s` |

Backend contract rollback can remove the additive response field only before the
frontend requires it. After cutover, revert consumers and the projection together;
never restore a first-party unconditional move as a compatibility fallback.
The native/sidebar move command and its browser conflict coverage remain pending
until separately qualified. This read-contract increment does not close M4.

## Read-contract qualification — 2026-10-07

Nine new cases failed before implementation because the editing base was absent.
The full outliner API plus legacy projection selection then produced **100 passes
and one expected-shape failure** (26.89 s). The explicit lightweight-field contract
was updated to include the new version; that case, OpenAPI and the sixteen
registered outliner statement/parameter scaling cases passed **18/18** (10.25 s).
The PostgreSQL mixed-search case passed **1/1** (29.67 s).

Frontend sidebar/query/factory/dependency tests passed **218/218** in four files
(13.39 s). App/UI/domain typechecks, full frontend lint and formatting (749 files),
backend Ruff and Pyright passed. The frontend's required version surfaced three
stale fixture shapes; they now use a shared lightweight factory or preserve the
version of the Multipart fixture. Mock API leaves also carry their actual Model
version. No field is defaulted in production to mask a missing version.

OpenAPI changed only by adding required positive `edit_version` to
`OutlinerModelRead`, `OutlinerModel` and `OutlinerMultipart` responses. Collection
matches are unchanged. These are additive response changes with no migration,
new query owner or additional per-row reads. The existing scale checks verify
bounded database work; they do not establish a frontend performance improvement.
Final broad backend/CI gates remain M11 work. The drag command cutover and its
real-browser regression remain pending; this checkpoint does not claim that
movement is already protected.


## Gesture and command cutover — assessed coverage

One Library move owner handles the command, its confirmed receipt and explicit
review. Pending commands are keyed by Model: a duplicate for that Model cannot
replace its intent, while unrelated Models may proceed independently. A small
recovery dialog presents failed commands; it owns no remote cache. The native
payload and sidebar capture are immutable gesture snapshots, including session.
Scope retirement aborts reviews and suppresses delayed publication/feedback.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | moves with the version captured by the native drag | Happy | Grid/list Model at version7 | PATCH sends If-Match7 and intended destination | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::moves with the version captured by the native %s drag` |
| C2 | rejects malformed or retired native drag data | Error | Bare id, bad JSON/fields/version, old session | No move request; invalid intent cannot become unconditional | Frontend unit | ✅ `src/lib/__tests__/model-dnd.test.ts::rejects incomplete or malformed payload %s; rejects a gesture from a retired session; src/components/__tests__/model-grid.test.tsx::ignores malformed native drag data %s` |
| C3 | freezes a sidebar move at drag start | Edge | Sidebar snapshot changes before drop | Callback retains original id/version/session/location | Frontend unit | ✅ `src/components/__tests__/filter-sidebar.test.tsx::keeps the gesture version when an outliner read changes during dragging` |
| C4 | moves a paginated sidebar leaf through the real API | Happy | Model beyond500 leaves absent from grid | Conditional PATCH; actual destination persists | Playwright | ✅ `tests/e2e-real/outliner-pagination.spec.ts::moves a model reached through keyboard pagination beyond 500 entries` |
| C5 | preserves a move destination after conflict | Error | PATCH412 | Source snapshot and intended destination retained; no automatic GET/retry | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::preserves the requested destination after a conflict without reading automatically` |
| C6 | reviews an uncertain move before retry | Error | PATCH network/503/malformed receipt | Retained intent; explicit review required | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::requires explicit review after %s` |
| C7 | retries a move with the explicitly reviewed version | Happy | Fresh Model read; user retries | Same destination; exact reviewed version; confirmed result visible | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::retries the original destination using exactly the reviewed version` |
| C8 | adopts the current location without moving | Happy | User discards move after review | Canonical current Model published; no additional PATCH | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::adopts the reviewed location without another write` |
| C9 | preserves move intent after failed review | Error | GET503 | No retry enabled; destination retained | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::keeps the intended destination when review fails` |
| C10 | hides private move details after denied review | Error | GET403/404 | No source/name/destination in recovery; no retry | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::hides private move details after review returns %s` |
| C11 | disables move retry after permission loss | Error | Fresh role view | Authorized read shown; retry disabled | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::disables retry when the reviewed role cannot edit` |
| C12 | requires fresh review after a repeated move conflict | Edge | Retry receives412 | Previous reviewed base cannot authorize another retry | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::requires a fresh review after a second conflict` |
| C13 | suppresses duplicate moves for the same Model | Edge | First command pending/awaiting review | One write; first destination retained | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::suppresses duplicate moves without replacing the first destination` |
| C14 | permits independent Models to move concurrently | Edge | Different Models; both requests held | Both writes start; either can finish without erasing the other | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::confirms unrelated Model moves while the first is pending` |
| C15 | suppresses retired move effects | Edge | Session/navigation/unmount changes while write pending | No late publication, refresh or success into new scope | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::ignores a late receipt after %s retirement` |
| C16 | aborts a dismissed move review | Edge | Review GET held; dismiss | Request aborted; late response cannot reopen recovery | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::aborts a dismissed review without reopening it` |
| C17 | preserves a confirmed move against an older detail read | Edge | GET started before PATCH acknowledgement | Acknowledged Model remains canonical | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::preserves a confirmed move when an older detail read completes late` |
| C18 | reports a rejected move without claiming success | Error | PATCH403/422 | Error feedback; no confirmed publication/refresh | Frontend unit | ✅ `src/features/library/__tests__/moves.test.tsx::does not publish a rejected write with HTTP %s` |
| C19 | requires a version for every first-party Model PATCH | Error | updateModel callers / invalid numeric base | Required TypeScript argument; invalid base rejected before HTTP | Frontend unit | ✅ `src/lib/api/__tests__/models/model.test.ts::rejects invalid editing version %s before HTTP` |
| C20 | recovers a real drag conflict explicitly | Happy | Another editor changes Model after drag snapshot | Real412; retained destination; reviewed retry persists | Playwright | ✅ `tests/e2e-real/outliner-pagination.spec.ts::moves a model reached through keyboard pagination beyond 500 entries` |

Removal: delete the id-only native payload and optional-version Model PATCH branch.
Rollback the drag sources, consumers and owner together; retain the additive
outliner version projection. Preserve existing M5 navigation edits when staging
shared Model card/grid paths.

## Command qualification — 2026-10-07

The native-drag regression first failed because the PATCH carried no If-Match.
The conditional command now uses the gesture snapshot, including for tree
Models absent from the current grid page. The sidebar regression replaces its
read during an active mouse gesture and verifies that the original editing
base reaches the destination callback. The id-only payload and optional-version
Model PATCH bypass have been removed. Collection moves retain their existing
contract.

The affected six frontend suites passed **317/317** (65.82 s), followed by the
new sidebar refresh-during-drag regression **1/1** (3.47 s). The real-backend
paginated tree flow passed **1/1** (24.5 s; total launch 1.0 min): a concurrent
edit after drag start yields a real412, the dialog retains the destination,
and the explicit reviewed retry persists using the exact reviewed version.
Rows C4 and C20 refer to that same browser flow, not two separate browser runs.
Final owner/native-drag tests passed **28/28** (5.31 s), including four
malformed native drop cases. Dependency-boundary tests passed **73/73**
(2.26 s); three selected suite-hygiene checks also passed. Full frontend lint,
formatting (**753 files**) and app/UI/domain typechecks passed. The existing
M5 browser file with top-level tests remains outside this increment; its known
hygiene failure has not been waived. No frontend performance improvement is claimed.

This closes the movement substep, not M4. Physical-restore editing-token identity
and the final M4 acceptance/removal audit remain before M5 may start.

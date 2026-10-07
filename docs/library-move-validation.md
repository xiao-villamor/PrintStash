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

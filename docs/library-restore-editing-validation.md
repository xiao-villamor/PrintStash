# Physical restore and editing identity — M4

## Contract under verification

Physical restore already renews `LibraryRevision.epoch` together with the private
publication marker. Entity metadata versions are copied exactly from the archive.
Consequently an integer version may recur in another database history. An open
draft must not become authorized merely because a restored entity later reaches
the same integer version. Reproduce that collision through the real restore owner
and HTTP editing endpoints before changing the contract.

The intended narrow design reuses the existing database incarnation with the
entity id and version. Editing snapshots carry that incarnation alongside their
version; conditional writes compare both atomically. The incarnation belongs to
the snapshot, never a later transport read. List/outliner/Source/detail projections
must expose the same editing base without per-row queries. No additional server
runtime, database column or frontend cache is justified for this invariant.
The conditional contract is unreleased work on this branch; update its readers,
writers and fixtures together rather than introducing a second first-party mode.
Legacy unconditional clients retain their explicitly documented compatibility
contract and do not acquire conditional-edit guarantees.

The initial preflight below was written before implementation. The compound
contract is now implemented and under qualification. M4 stays active; M5 work
remains deferred. Coverage status names tests; actual run results are separate.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| R1 | rejects a draft from the previous restored history | Error | Model/Multipart/Document version recurs after physical restore | Old If-Match returns412; current content unchanged | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_restore.py::TestRestoredEditingIdentity::test_rejects_a_draft_from_the_previous_restored_history` |
| R2 | exposes one coherent editing base | Happy | Detail/list/browse/outliner/Source reads | Required incarnation corresponds to versioned projection | Integration | ✅ `backend/tests/integration/modules/library/test_edit_preconditions.py::TestEditingHistory::test_reads_the_database_incarnation_with_the_entity` |
| R3 | rejects a retired incarnation during atomic claim | Error | Same entity/version; different epoch |412 without advancing version or changing metadata | Integration | ✅ `backend/tests/integration/modules/library/test_edit_preconditions.py::TestEditingHistory::test_rejects_the_previous_database_incarnation` |
| R4 | accepts the current incarnation | Happy | Current epoch/version/id | Conditional mutation persists and returns its new base | Integration | ✅ `backend/tests/integration/modules/library/test_edit_preconditions.py::TestConditionalEdit::test_performs_conditional_metadata_edit` |
| R5 | refuses malformed editing identity | Error | Missing/malformed epoch or mismatched entity | Conditional write rejected before side effects | Integration | ✅ `backend/tests/integration/modules/library/test_edit_preconditions.py::TestConditionalEdit::test_rejects_wrong_etag` |
| R6 | preserves the captured batch editing base | Edge | Selection/undo created before restore | Batch cannot authorize stale-history versions | Integration | ✅ `backend/tests/integration/modules/library/test_edit_preconditions.py::TestEditingHistory::test_rejects_a_batch_from_an_old_history` |
| R7 | rejects stale cover identity before publishing bytes | Error | Source/Multipart cover command from old history | Existing cover bytes/ownership unchanged | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestConditionalProvenance::test_preserves_source_cover_when_old_history_deletes; backend/tests/integration/modules/library/test_edit_preconditions.py::TestEditingHistory::test_rejects_cover_bytes_from_an_old_history` |
| R8 | retains the restored incarnation on forward retry | Edge | Lost swap acknowledgement | Recovery keeps the already-published editing identity | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_restore.py::TestRestoredLibraryAuthority::test_post_swap_ack_retry_keeps_published_epoch` |
| R9 | isolates repeated physical restores | Edge | Same archive restored twice | Neither restored editing identity authorizes the other | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_restore.py::TestRestoredLibraryAuthority::test_repeated_physical_restore_never_reuses_archived_epoch` |
| R10 | preserves the editing identity through database transfer | Happy | SQLite/Postgres transfer without restore | Same logical history retains its editing base | Integration/PostgreSQL | ✅ `backend/tests/integration/modules/administration/test_database_transfer.py::TestLibraryContractTransfer::test_preserves_versioned_library_through_database_transfer` |
| R11 | enforces the identity after PostgreSQL restore | Error | Real PostgreSQL replacement | Prior-history conditional draft rejected | E2E/PostgreSQL | ✅ `backend/tests/e2e/test_postgres_backup.py::TestPostgresBackup::test_rejects_document_draft_from_restored_history` |
| R12 | freezes frontend editing identity with the draft | Edge | Snapshot replaced while a form/gesture is active | Write carries its original epoch/version pair | Frontend unit | ✅ `frontend/src/components/model-detail/__tests__/index.test.tsx::preserves the editing base across a background refresh` |
| R13 | retries only against the explicitly reviewed identity | Happy | Conflict then review then retry | Exact reviewed epoch/version used; draft preserved | Frontend unit | ✅ `frontend/src/components/model-detail/__tests__/index.test.tsx::saves a retained Model draft only after explicit version review` |
| R14 | rejects malformed conditional acknowledgements | Error | Missing/mismatched epoch or invalid version | No confirmed publication; explicit recovery | Frontend unit | ✅ `frontend/src/lib/api/__tests__/models/model.test.ts::rejects an acknowledgement with $label identity; frontend/src/lib/api/__tests__/documents.test.ts::rejects an acknowledgement with $label identity; frontend/src/lib/api/__tests__/provenance.test.ts::rejects a $label cover acknowledgement` |
| R15 | preserves bounded read work | Edge | Larger library/batched projections | No per-row incarnation lookup | Integration | ✅ `backend/tests/repo/test_read_scaling.py` |
| R16 | rejects an acknowledgement after a newer history was observed | Edge | Old write held; canonical Query receives another epoch | Older-history receipt cannot replace the newly observed history | Frontend unit | ✅ `frontend/src/features/library/__tests__/model-detail.test.tsx::rejects a receipt after a different history has become canonical; frontend/src/lib/queries/__tests__/documents.test.tsx::preserves a restored document after an earlier history acknowledges` |
| R17 | adopts a reviewed restored history with a lower counter | Happy | Explicit review returns another epoch at version1 | Correct new history replaces old larger counter; draft retained until decision | Frontend unit | ✅ `frontend/src/features/library/__tests__/model-detail.test.tsx::publishes an explicitly read history with a lower counter` |

Rollback readers and conditional writers as one contract. Keep existing durable
restore publication/recovery evidence unchanged. Final acceptance must include
OpenAPI, affected SQLite/Postgres cases, frontend behavioral regressions and the
applicable browser contracts. No performance improvement is presumed.

## Reproduction — 2026-10-07

The three real SQLite restore/HTTP regressions fail at the intended assertion:
`200 == 412` (10.71 s). Each verifies that the new history has reached the same
integer version before submitting the older ETag. The obsolete draft is actually
persisted for Model, Multipart and Document. This is a confirmed correctness
failure; no restore production code has been changed in this preflight.

The existing singleton can supply a read-only ORM expression included with each
aggregate SELECT, rather than a separately timed per-row lookup. Its value must
travel with the returned DTO as `edit_epoch`, paired with `edit_version`.
Outliner scalar projections and Source reads must carry the same pair explicitly.
Claims compare the epoch inside the conditional UPDATE; publication and review
must never combine a version from one history with an epoch fetched later.
Qualification must verify that this expression does not become a physical table
column or change portable database copies, and that list queries remain bounded.

## Qualification in progress — 2026-10-07

- SQLite physical restore collision: three genuine failures before the change;
  three passes afterward (10.49 s).
- Conditional editing, provenance and outliner: 223 passed (70.57 s).
- Restore/recovery, database transfer and real PostgreSQL restore: 13 passed
  (248.63 s). This includes repeated restore and forward recovery identity.
- Retired-history claims, cover bytes and bounded read work: 86 passed (25.87 s).
- Frontend owners and affected editors: 219 passed (22.44 s); the follow-up
  wire contracts, dependency boundaries and cross-history Model draft/review
  checks passed 197 tests (19.30 s). Counts overlap and are not summed.
- OpenAPI regenerated: one passing contract check (6.03 s); reviewed diff adds
  the editing pair and changes batch values to that pair, without other routes.
- App/UI/domain TypeScript, backend Pyright, frontend lint/756-file formatting,
  backend Ruff and the configured backend formatting scope passed. Vite production
  build passed (5.77 s); its existing >500 kB chunk advisories remain.
- Six real Chromium flows verified: Document conflict/lost acknowledgement,
  Multipart auxiliary conflict, paginated drag, Source editing, and quick tags.
  Initial run: five passed, one obsolete numeric-batch assertion failed; correcting
  that assertion yielded one passing targeted rerun (22.5 s; total 1.7 min).
- Four mock-API Chromium flows passed (28.7 s): ambiguous Model save, explicit
  Model retry, Multipart composition retry and batch undo conflict.
- Document cache/history feedback final regression: 43 passed (8.44 s).
  Source adoption passed in the 15-pass/one-failure owner run; the Multipart failure
  asserted before the asynchronous Query render. Awaiting the visible result
  produced six passing Multipart tests (2.30 s).
- The wider backend fast lane is running with a three-failure stop. It is not yet
  a green-gate claim. Full backend/remote CI and the complete M4 acceptance audit
  remain open.

No performance improvement is claimed. Bounded query work is a correctness and
scaling contract, not an end-user latency measurement. Raw logs and source
provenance are retained in the local implementation evidence directory.

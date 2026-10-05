# Cooperative batch-import cancellation

A Job runner binds withdrawal to its current execution attempt and epoch for each
step. Import loops, source preparation, extraction and hashing use the scoped
checkpoint. A forced check before an entry or Artifact refuses withdrawn work;
checks during source blocks poll durable state at most once per 200 ms. Once
withdrawal is observed, that execution remains stopped. The core helpers accept
pure callbacks and have no application or database dependency.

Cancellation unwinds only owned, disposable staging. Browser upload-slot sources,
review archives and confirmed Artifacts remain available. A retry can reuse an
Artifact already committed before cancellation. Before a domain commit, receipt
cleanup rolls back only the publication proven to belong to that operation;
a cleanup failure retains uncertain bytes and preserves the primary cancellation.
Browser and slot staging copies calculate their content hash during the same
copy, preserving bytes without a separate hashing pass for those copies.

## Verification

The matrix records 26 behaviours and 31 parametrized cases, all verified. Focused
core file tests passed **116 cases in 1.36 seconds**. Affected backend/API owner
checks verify **172 distinct cases**, including the real DBOS workflow and the
whole-app API test. Focused suite hygiene passed **171 cases**. These are **459
distinct passing cases** across the selections, deduplicated from JUnit results.

The combined backend selection initially passed 62 of 67 cases: five new batch
assertions included the suite's existing sentinel File. The owner selection then
passed 117 of 118 cases: the new cleanup assertion used an obsolete destination
layout. The corrections preserve all baseline File snapshots and verify the
actual publication receipt. The final receipt/probe/hygiene selection passed
174 cases in 23.40 seconds. No failed test remains unresolved.

Ruff and the repository's configured Pyright gate passed. Explicitly checking
all changed ingestion files also exposes seven existing inbox type errors; the
unchanged baseline produces the same seven errors. No new scoped type error is
introduced. Full local suites and Deep CI were deferred; normal PR CI supplies
the repository gate. Parameter cases share one behaviour row.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | cancels a copy between source blocks | Error | Multi-block source; callback raises BaseException after first block | Exact exception escapes; unread source remains; no destination or private staging temp | Unit | ✅ `packages/printstash-core/tests/files/test_storage.py::TestStreamCancellation::test_cancels_a_copy_between_source_blocks` — verified |
| 2 | refuses publication after cancellation | Error | Source fully copied; callback cancels before publication | No destination or staging temp; exact cancellation preserved | Unit | ✅ `packages/printstash-core/tests/files/test_storage.py::TestStreamCancellation::test_refuses_publication_after_cancellation` — verified |
| 3 | removes completed entries after boundary cancellation | Error | First ZIP entry staged; second entry callback raises BaseException | Owned staging outputs removed; original ZIP unchanged; exact cancellation preserved | Unit | ✅ `packages/printstash-core/tests/files/test_archives.py::TestExtractionCancellation::test_removes_completed_entries_after_boundary_cancellation` — verified |
| 4 | cancels an archive entry between blocks | Error | Selected entry exceeds one block; chunk callback cancels | No staged output or temp; original ZIP unchanged | Unit | ✅ `packages/printstash-core/tests/files/test_archives.py::TestExtractionCancellation::test_cancels_an_archive_entry_between_blocks` — verified |
| 5 | preserves a replacement during cancellation cleanup | Edge | Another writer replaces first completed entry before cancellation | Replacement bytes remain; original ZIP unchanged; cancellation escapes | Unit | ✅ `packages/printstash-core/tests/files/test_archives.py::TestExtractionCancellation::test_preserves_a_replacement_during_cancellation_cleanup` — verified |
| 6 | preserves cancellation when cleanup fails | Error | Cancellation follows a completed entry; unlink raises OSError | Original cancellation escapes; source ZIP preserved; failed-cleanup output remains intact | Unit | ✅ `packages/printstash-core/tests/files/test_archives.py::TestExtractionCancellation::test_preserves_cancellation_when_cleanup_fails` — verified |
| 7 | preserves a collided destination | Error | Second selected output collides with an existing destination | Existing bytes preserved; first owned output removed; source ZIP unchanged | Unit | ✅ `packages/printstash-core/tests/files/test_archives.py::TestExtractionCancellation::test_preserves_a_collided_destination` — verified |
| 8 | stops hashing at cancellation boundary | Error | Callback cancels between source blocks or immediately before completion | Original BaseException escapes; unread bytes remain; caller stream stays open | Unit | ✅ `packages/printstash-core/tests/files/test_hashing.py::TestHashCancellation::test_stops_hashing_at_cancellation_boundary` — verified |
| 9 | closes owned file after hash cancellation | Error | Real source file; callback cancels between blocks or before completion | Original BaseException escapes; owned descriptor closed; source bytes unchanged | Unit | ✅ `packages/printstash-core/tests/files/test_hashing.py::TestHashCancellation::test_closes_owned_file_after_hash_cancellation` — verified |
| 10 | preserves digest with a cooperative callback | Happy | Identical multi-block file and caller-owned stream | Both hashes equal independent hashlib digest; bytes unchanged; caller stream remains open | Unit | ✅ `packages/printstash-core/tests/files/test_hashing.py::TestHashCancellation::test_preserves_digest_with_a_cooperative_callback` — verified |
| 11 | stops after the committed Artifact | Error | Real running Job; first G-code Artifact committed; cancel or retry epoch; flat or grouped batch | First File remains readable, second absent, OperationCancelled propagates, private input staging cleaned | Integration | ✅ `integration/modules/ingestion/importer/test_cancellation.py::TestBatchWithdrawal::test_stops_after_the_committed_artifact` — verified |
| 12 | reuses the committed Artifact on retry | Edge | Same Job retried with restored staged bytes | First File identity reused; exactly two Files after retry | Integration | ✅ `integration/modules/ingestion/importer/test_cancellation.py::TestBatchWithdrawal::test_retry_reuses_the_committed_artifact` — verified |
| 13 | releases staging after chunk cancellation | Error | Real Job canceled between HTTP chunks with actual capacity reservation | No final/private file or live reservation; stream closed | Integration | ✅ `integration/modules/ingestion/importer/test_cancellation.py::TestDownloadWithdrawal::test_releases_staging_after_chunk_cancellation` — verified |
| 14 | cleans owned outputs after partial extraction | Error | Selected ZIP first output complete, second partially copied; archive lease held | Both extracted outputs removed, archive remains, cancellation propagates | Integration | ✅ `integration/modules/ingestion/importer/test_cancellation.py::TestExtractionWithdrawal::test_cleans_owned_outputs_after_partial_extraction` — verified |
| 15 | stops before resolving the next member | Error | First member staged, Job canceled | Next member never resolved; prior owned path removed | Integration | ✅ `integration/modules/ingestion/test_background.py::TestBatchWithdrawal::test_stops_before_resolving_the_next_member` — verified |
| 16 | stops before downloading the next selection | Error | First selected URL staged, Job canceled | Next download never starts; prior owned path removed | Integration | ✅ `integration/modules/ingestion/test_background.py::TestBatchWithdrawal::test_stops_before_downloading_the_next_selection` — verified |
| 17 | cancels a selected archive before execution | Edge | Real archive inspect/select API then public queued Job cancellation | Job remains canceled after drain; no selected Artifacts imported | E2E | ✅ `e2e/test_batch_import_cancellation.py::TestBatchImportCancellation::test_cancels_selected_archive_before_execution` — verified |
| 18 | stops the actual engine batch after a committed Artifact | Error | Actual DBOS workflow with canonical importer; cancel after real first Artifact commit | Step unwinds; Job canceled; exactly one readable File; second private input removed | Integration | ✅ `integration/modules/ingestion/test_jobs.py::TestDbosBatchWithdrawal::test_cancellation_stops_the_batch_after_a_committed_artifact` — verified |
| 19 | refuses browser copy for withdrawn attempt | Edge | Leased local source, cancelled scope | Original remains; no disposable copy returned/published | Integration | ✅ `integration/modules/ingestion/inbox/test_staging.py::TestStageLocalCaptureAssets::test_refuses_browser_copy_for_withdrawn_attempt` — verified |
| 20 | stops slot copying between entries | Edge | Two durable slot sources; withdrawal after first copy | No second copy; own first copy cleaned; both slot sources unchanged | Integration | ✅ `integration/modules/ingestion/inbox/test_staging.py::TestStageCaptureUploadSlotAssets::test_stops_slot_copying_between_entries` — verified |
| 21 | rolls back precommit bytes on cooperative cancellation | Error | Receipt published; Metadata constructor raises OperationCancelled | Original cancellation; no File/Metadata rows; exact owned blob removed | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_rolls_back_precommit_bytes_on_cooperative_cancellation` — verified |
| 22 | preserves digest of browser copy | Happy | Original browser bytes copied with one pass | Copied bytes unchanged and digest equals independent SHA256 | Integration | ✅ `integration/modules/ingestion/inbox/test_staging.py::TestStageLocalCaptureAssets::test_preserves_the_digest_of_a_browser_copy` — verified |
| 23 | preserves collided staging destination | Error | Destination already contains foreign bytes | FileExistsError, existing bytes unchanged, own temp removed | Integration | ✅ `integration/modules/ingestion/inbox/test_staging.py::TestCopyImportSource::test_preserves_a_collided_destination` — verified |
| 24 | bounds cancellation probes during archive review | Edge | One 8 MiB ZIP member with at least eight block boundaries and a fixed probe clock | Fewer than eight withdrawal queries, complete manifest and source retained | Integration | ✅ `integration/modules/ingestion/test_background.py::TestInspectUploadedArchive::test_bounds_cancel_probes_during_archive_review` — verified |
| 25 | preserves committed Artifact after cooperative cancellation | Edge | Real domain commit succeeds then raises the cancellation | Same cancellation propagates; fresh session sees File and Metadata; exact bytes retained | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_preserves_committed_artifact_after_cooperative_cancellation` — verified |
| 26 | preserves cancellation when receipt cleanup fails | Error | Metadata cancellation after publication; exact receipt cleanup raises OSError | Same cancellation with cleanup note; transaction rolled back; uncertain bytes retained | Integration | ✅ `integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_preserves_cancellation_when_receipt_cleanup_fails` — verified |

## Limits

Cancellation is cooperative. A task waiting for an HTTP response reaches its
next checkpoint after that wait or its configured transport timeout; cancellation
is not an immediate network interrupt.

Source preparation is checked between bounded blocks. Fallback publication by
exclusive COPY is complete once admitted: it does not abort an uncertain partial
destination or unlink a possible replacement. Consumers expose the result only
after publication succeeds.

Core extraction cleanup applies to caller-exclusive, randomly named private
staging outputs. It checks the recorded regular-file device and inode before
best-effort unlink and preserves mismatches. This is not an atomic conditional
delete guarantee against an external process replacing the pathname between that
check and unlink. The original archive is never an extraction cleanup target.

These changes require no database migration, external queue or cloud service.

# Sustained ingestion controls

A new `POST /api/v1/ingest/model` creates a new ingestion key and a versioned Artifact under the existing source-hash Model. Retrying the same durable Job must reuse its committed Artifact. Sustained qualification previously submitted a new POST while labelling it replay and requiring Artifact reuse; that expectation contradicted the versioned-Artifact contract in [CONTEXT.md](../../CONTEXT.md).

Repeated uploads now have the distinct `reupload` purpose, remain excluded from fresh Artifact credit, and require completed original-byte/thumbnail verification plus fresh SQL evidence: the same source hash and Model, different File IDs and ingestion keys. They do not replace same-Job retry proofs. The `replay` purpose and its no-fresh-credit contract remain separate. Malformed-source controls keep their refusal and unchanged-source requirements.

Qualification checkpoints publish accumulated errors immediately. A failed source or mandatory control prevents another source/control from starting and enters the ordinary owned drain. Short runs remain smoke controls; they cannot meet the two-hour/1,000-fresh-Artifact qualification threshold. `--control-every-artifacts` changes SOAK control cadence (default 50) to exercise the real control in finite small fixtures. Original failed reports are never rewritten as successes.

| # | Behaviour | Category | Precondition / input | Observable outcome | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Repeated upload proves a new version | Edge | Matching source; changed Model/hash/key/File identity variants | Only same Model/source with different ingestion key and Artifact proves a new version | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestReuploadIdentity::test_requires_a_new_version_of_the_same_source` |
| 2 | Repeated uploads supply no fresh credit | Edge | Completed fresh sample plus completed repeated upload | Exactly one fresh usable Artifact; distinct fresh/reupload purposes | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestReuploadIdentity::test_excludes_reupload_from_fresh_credit` |
| 3 | Failure checkpoint refuses more work | Error | Mandatory-control failure | Error is written before returning refusal; later error-list mutations cannot rewrite checkpoint state | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestQualificationCheckpoint::test_records_failure_before_another_work_unit` |
| 4 | Clean checkpoint admits more work | Happy | No accumulated errors | Empty error journal and affirmative continuation result | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestQualificationCheckpoint::test_admits_more_work_after_a_clean_checkpoint` |
| 5 | SQL evidence observes committed identity | Happy | Persisted Artifact with ingestion key | Exact File ID, Model ID, key and source hash | Integration | ✅ `tests/integration/scripts/test_qualify_ingestion.py::TestArtifactIdentity::test_reads_committed_ingestion_identity` |
| 6 | Absent Artifact supplies no proof | Error | Missing File ID | Explicit identity-missing error | Integration | ✅ `tests/integration/scripts/test_qualify_ingestion.py::TestArtifactIdentity::test_refuses_missing_artifact` |
| 7 | Legacy Artifact supplies no ingestion-key proof | Error | Artifact without ingestion key | Explicit ingestion-key-missing error | Integration | ✅ `tests/integration/scripts/test_qualify_ingestion.py::TestArtifactIdentity::test_refuses_artifact_without_ingestion_identity` |
| 8 | Actual finite qualification proves repeated upload | Happy | Private actual app, DBOS, native workers, two fresh inputs and cadence one | Both fresh outputs usable; repeated upload has same Model/different key; clean drain; user vault unchanged; smoke never qualifies as sustained run | Integration | ✅ `tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_runs_a_private_smoke` |
| 9 | Mandatory failure stops the workload | Error | Actual control with final verdict fault-injected; ten requested fresh inputs | Stops after two fresh inputs; failure and unmet requested threshold preserved; natural drain/cleanup quiescent | Integration | ✅ `tests/integration/scripts/test_qualify_ingestion.py::TestMandatoryControlFailure::test_stops_before_another_fresh_ingestion` |
| 10 | Invalid cadence is refused before startup | Error | Cadence zero or above 10,000 | CLI error, no output directory or app startup | Integration | ✅ `tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_rejects_invalid_requests` |
| 11 | Replay cannot manufacture fresh credit | Error | Replay incorrectly returns another Artifact | No extra fresh usable credit; replay remains its distinct purpose | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestQualificationCredits::test_excludes_replay_that_creates_a_fresh_artifact` |
| 12 | Unverified repeated observation supplies no proof | Error | Failed/refused/timeout, wrong SHA/size, unverified bytes/preview, unknown source, reused or absent Artifact | Negative verdict with no SQL identity evidence | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestReuploadControl::test_rejects_unverified_repeated_observation` |
| 13 | Unverified original supplies no proof | Error | Original failed, unverified bytes/preview or absent Artifact | Negative verdict with no SQL identity evidence | Unit | ✅ `tests/unit/scripts/test_qualify_ingestion.py::TestReuploadControl::test_rejects_unverified_original_observation` |

The initial focused regression has 11 failures before the implementation. The final unit/SQL selection passes **99 cases in 5.65s**; the two invalid-cadence cases pass in **3.17s**. The actual healthy worker completes in **83.04s**, with two fresh Artifacts and one valid repeated-upload control. The first fault fixture omitted the worker's pre-created output directory and failed before application startup; after fixing that fixture, the actual injected-failure selection passes **1 case in 88.54s**. These overlapping selections are not summed. No local full, coverage or Deep CI suite was run.

## E2E database startup

The E2E fixture uses a private on-disk database and must install the production connection hook before schema creation. Activating WAL after readers start can fail immediately when a newly opened worker or HTTP connection attempts the journal-mode transition. Native search closes the setup session and disposes idle pooled connections after enabling the installed vector extension, before starting its worker. The base connection hook stays registered throughout the test.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 14 | Starts with WAL journaling | Happy | Fresh private E2E database | First connection reports WAL before workers start | E2E | ✅ `tests/e2e/test_database_connections.py::TestE2EDatabaseConnections::test_starts_with_wal_journaling` |
| 15 | Commits a new connection during a reader | Edge | Open reader snapshot; separate writer connection | Writer commits while reader retains old snapshot; new observer sees committed value | E2E | ✅ `tests/e2e/test_database_connections.py::TestE2EDatabaseConnections::test_commits_a_new_connection_during_a_reader` |
| 16 | Serves hybrid queries through HTTP | Happy | Actual HTTP app, inference fake, search worker | Indexed search returns the expected document through HTTP | E2E | ✅ `tests/e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_serves_hybrid_queries_through_http` |
| 17 | Switches to native index without reembedding | Edge | Existing generation; float32/int8/binary native index | Native generation becomes active without additional embedding calls | E2E | ✅ `tests/e2e/test_search_generations.py::TestSearchGenerationLifecycle::test_switches_to_native_index_without_reembedding` |

Before the fixture correction, the two new connection regressions fail in **54.89s**: DELETE journal mode and a writer commit blocked by the reader. After correction, the focused connection/HTTP/native-index selection passes **6 cases in 14.16s**. The earlier CI E2E setup error and hosted browser shutdown remain preserved as separate failures; this focused result does not establish the full CI gate.

The affected search-generation lifecycle file also passes **9 cases in 67.18s**, including continuous readers, restoration without the optional extension, and process-loss recovery. Its cases overlap the earlier focused selection; counts are not summed.

## Backup fixture integrity

E2E databases also retain production foreign-key enforcement. Backup restore arrangements permanently purge their uploaded model through the real API, then assert that the payload is absent before restoring it. They no longer issue a partial raw SQL deletion that leaves referencing rows behind. This exercises restoration after a permanent purge; direct database-corruption recovery remains a separate service-level contract.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 18 | Backup restore recovers a permanently purged model | Happy | Uploaded G-code backed up, then model purged | Model absent before restore; recovered model name and exact original bytes | E2E | ✅ `tests/e2e/test_backup.py::TestBackupRestore::test_backup_wipe_restore_round_trips_through_the_real_api` |
| 19 | Backup can be taken after restoring one | Edge | Backup restored after permanently purging its model | Second backup completes with a different backup ID | E2E | ✅ `tests/e2e/test_backup.py::TestBackupRestore::test_a_backup_can_be_taken_after_restoring_one` |
| 20 | DXF original survives backup restore | Happy | DXF backed up, then model purged | Purged payload absent; restored download matches original bytes | E2E | ✅ `tests/e2e/test_backup.py::TestBackupRestore::test_dxf_original_survives_backup_restore` |
| 21 | Orphaned foreign keys are refused | Error | Child referencing an absent parent in private E2E DB | Real IntegrityError; no child committed | E2E | ✅ `tests/e2e/test_database_connections.py::TestE2EDatabaseConnections::test_refuses_orphaned_foreign_keys` |

CI37380569585 preserves **155 passing E2E cases and two failures in 340.66s**. Both failures occur during raw SQL preparation of backup scenarios, with foreign-key enforcement rejecting deletion of a referenced File. The browser smoke and the other fourteen test jobs succeed. No retry or constraint weakening hides these failures.

The corrected backup/connection selection passes **6 cases in 21.87s**, including the three restore scenarios and actual foreign-key refusal. No local full, coverage or Deep CI was run.

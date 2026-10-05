# Ingestion publication and recovery

Managed publication separates durable intent, physical creation and domain
adoption. A publication handle carries the reservation generation and provider
identity; a database row ID or a reusable storage key is insufficient authority.
Domain owners (Job, Artifact or source) are locked before the locator anchor.
Preparation performs storage probes outside those locks; adoption checks only
prepared evidence and SQL authority. Physical cleanup follows an independently
committed retirement record and an exact receipt.

Provider fingerprints and exact receipt conversion live in a shared storage
contract. Backup destination resolution owns read and exact-delete capabilities;
replica publication belongs to backup orchestration. This keeps provider
resolution independent of publication and retirement orchestration.

Retirement history is retained even after bytes disappear. This prevents late
completion, database ID reuse or equal replacement bytes from reviving an old
handle. A new physical generation can reuse the canonical locator with fresh
authority. Unversioned remote receipts cannot safely distinguish recreation
with the same ETag, so cleanup retains blocked evidence rather than repeatedly
attempting deletion. This trades retained history and deferred cleanup for a
recoverable, fail-closed ownership decision. Downgrade refuses to erase active
custody or retirement authority.

Scratch windows record their charge, writer lock and directory identity before
payload bytes. Sealed outputs retain exact file identity; an input lease can
protect a handoff before the final custody update. Recovery defers live writers,
preserves replacements and keeps durable credit when cleanup fails. Cleanup is
a Job Definition with bounded discovery. A new execution retires the previous
epoch's scratch before admitting another window; nested windows in the same
epoch remain valid.

Source-cover HTTP writes publish an immutable private candidate before changing
the source pointer. Pointer and receipt adoption commit together. A failed commit
retires only the unadopted candidate; a lost acknowledgement preserves a candidate
that committed. Deletion revalidates the prepared pointer under the source lock.
Legacy replacement compensation cannot resurrect a COMMITTED owner after the
cover was deleted concurrently.

## Reading the matrices

Each row describes an observable contract. **✅ means the named test is present,
not that this document's author executed it.** Parameter variants stay in one
row. Test nodes use repository-relative paths and identify the current owners.
Execution evidence is listed separately below. An authored E2E test or a tested
qualification harness is not measured native, LOAD, image or soak qualification.

## Publication races

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | adoption commit wins over a waiting sweep | Edge | Adopter holds locator; sweep waits | Committed owner and bytes survive sweep | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestPublicationTransactions::test_adoption_commit_wins_over_a_waiting_sweep` |
| 2 | retirement commit rejects a waiting adopter | Edge | Collector retires locator while adopter waits | Old candidate rejected; no ownership revival | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestPublicationTransactions::test_retirement_commit_rejects_a_waiting_adopter` |
| 3 | first anchor insert serializes an adopter without a reservation | Edge | First anchor insertion races adoption | New anchor serializes exclusion and rejects revoked authority | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestPublicationTransactions::test_first_anchor_insert_serializes_an_adopter_without_a_reservation` |
| 4 | thumbnail claims job before holding its publication anchor | Edge | Real Job lock contends with thumbnail adopter | Job authority claimed before anchor; no reversed lock order | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestPublicationTransactions::test_thumbnail_claims_job_before_holding_its_publication_anchor` |
| 5 | concurrent first reservations have one current winner | Edge | Two first reservations for one locator | Only one current reservation wins | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestFirstReservation::test_concurrent_first_reservations_have_one_current_winner` |
| 6 | retired s3 receipt cannot delete the new generation | Edge | Old version retired; newer generation published | Exact old version cleanup preserves new object | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestVersionedBackupRetirement::test_retired_s3_receipt_cannot_delete_the_new_generation` |
| 7 | unversioned outbox is retained without repeated delete attempts | Error | Unversioned remote receipt; recreated ETag possible | Durable blocked intent retained without repeated deletes | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestVersionedBackupRetirement::test_unversioned_outbox_is_retained_without_repeated_delete_attempts` |
| 8 | source lock serializes two prepared cover pointers | Edge | Two candidates prepared for one source | Source lock permits one pointer winner | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSourceCoverTransactions::test_source_lock_serializes_two_prepared_cover_pointers` |
| 9 | old absence probe yields to committed cover adoption | Edge | Absence observed before concurrent cover adoption | Committed cover and receipt survive stale cleanup | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSourceCoverTransactions::test_old_absence_probe_yields_to_committed_cover_adoption` |

## Retirement and generation authority

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 10 | rejects an exact receipt after orphan retirement | Error | Exact creation receipt completes after retirement | Retired authority cannot become committed | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestRecordCreation::test_rejects_an_exact_receipt_after_orphan_retirement` |
| 11 | preserves legacy creation without a reservation | Edge | Legacy exact creation has no reservation | Legacy creation remains usable through explicit compatibility | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestRecordCreation::test_preserves_legacy_creation_without_a_reservation` |
| 12 | reuses a canonical locator with a distinct reservation | Happy | Previously reserved canonical locator reused | New reservation has distinct authority | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestReserveCreation::test_reuses_a_canonical_locator_with_a_distinct_reservation` |
| 13 | retires a late receipt without adopting a new reservation | Edge | Late old receipt arrives after newer reservation | Old receipt retires; newer authority is untouched | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestCompletePublication::test_retires_a_late_receipt_without_adopting_a_new_reservation` |
| 14 | retains evidence when receipt recovery is deferred | Error | Physical receipt cannot be recovered confidently | Unresolved evidence retained for retry | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestIncompleteRetirement::test_retains_evidence_when_receipt_recovery_is_deferred` |
| 15 | does not infer old ownership from new reservation bytes | Error | Same locator now belongs to new reservation | New bytes do not certify old ownership | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestIncompleteRetirement::test_does_not_infer_old_ownership_from_new_reservation_bytes` |
| 16 | legacy transfer wins before late creator cleanup | Edge | Legacy transfer commits before creator compensation | Adopted owner survives late cleanup | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestIncompleteRetirement::test_legacy_transfer_wins_before_late_creator_cleanup` |
| 17 | late creator cleanup commits even when caller rolls back | Error | Caller rolls back after late creation | Cleanup authority persists independently | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestIncompleteRetirement::test_late_creator_cleanup_commits_even_when_caller_rolls_back` |
| 18 | adoption performs no backend io | Happy | Prepared receipt; storage calls forbidden during adoption | SQL adoption succeeds without backend I/O | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestPreparedPublication::test_adoption_performs_no_backend_io` |
| 19 | old handle cannot adopt after database id reuse | Error | Database ID reused for another reservation | Old handle rejected by generation identity | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestPreparedPublication::test_old_handle_cannot_adopt_after_database_id_reuse` |
| 20 | retry retains the original creation authority | Edge | Retry observes an existing reservation | Original authority retained rather than recreated | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestReservePublication::test_retry_retains_the_original_creation_authority` |
| 21 | refuses a retry observation that an adopter replaced | Edge | Adoption changes authority during retry observation | Stale retry refused | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestReservePublication::test_refuses_a_retry_observation_that_an_adopter_replaced` |
| 22 | retires an unadopted candidate before cleanup | Happy | Private candidate is abandoned | Durable retirement precedes exact cleanup | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestAbandonPublication::test_retires_an_unadopted_candidate_before_cleanup` |
| 23 | preserves a candidate committed by a concurrent owner | Edge | Concurrent owner commits before abandon | Committed candidate survives compensation | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestAbandonPublication::test_preserves_a_candidate_committed_by_a_concurrent_owner` |
| 24 | unresolved history does not starve the next pending candidate | Edge | Old unresolved history before later pending candidate | Later candidate gets a sweep turn | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestSweepFairness::test_unresolved_history_does_not_starve_the_next_pending_candidate` |
| 25 | equal new bytes at a revoked locator have independent authority | Edge | Identical content recreated at revoked locator | Fresh authority succeeds; old authority stays revoked | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestCanonicalReadoption::test_equal_new_bytes_at_a_revoked_locator_have_independent_authority` |
| 26 | keeps exact revocation after immediate deletion | Happy | Owned key removed immediately | Exact revocation remains recorded | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestDeleteOwnedKey::test_keeps_exact_revocation_after_immediate_deletion` |
| 27 | stale failure cannot block a concurrent success | Edge | Backup reconciliation failure races committed success | Stale failure cannot downgrade success | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestReconcileBackupCaches::test_stale_failure_cannot_block_a_concurrent_success` |
| 28 | new logical token cannot readopt a revoked immutable version | Error | New token names same retired physical version | Immutable version remains revoked | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestRemotePhysicalGeneration::test_new_logical_token_cannot_readopt_a_revoked_immutable_version` |
| 29 | new immutable version can reuse the retired locator | Happy | Same locator contains a different immutable version | New physical generation can be adopted | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_retirement.py::TestRemotePhysicalGeneration::test_new_immutable_version_can_reuse_the_retired_locator` |

## Storage retirement migration

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 30 | preserves populated receipts through upgrade roundtrip | Happy | Existing receipt rows before upgrade | Ownership data survives upgrade roundtrip | Integration | ✅ `backend/tests/integration/db/migrations/test_storage_retirement.py::TestStorageRetirementUpgrade::test_preserves_populated_receipts_through_upgrade_roundtrip` |
| 31 | retired history allows one new active generation | Edge | Retired receipt history plus new reservation | One active generation permitted alongside history | Integration | ✅ `backend/tests/integration/db/migrations/test_storage_retirement.py::TestStorageRetirementUpgrade::test_retired_history_allows_one_new_active_generation` |
| 32 | rejects invalid publication authority | Error | Invalid authority field combinations | Database rejects invalid receipt states | Integration | ✅ `backend/tests/integration/db/migrations/test_storage_retirement.py::TestStorageRetirementUpgrade::test_rejects_invalid_publication_authority` |
| 33 | downgrade refuses to erase retirement authority | Error | Existing retirement authority before downgrade | Downgrade refuses destructive loss | Integration | ✅ `backend/tests/integration/db/migrations/test_storage_retirement.py::TestStorageRetirementUpgrade::test_downgrade_refuses_to_erase_retirement_authority` |
| 34 | offline postgres render preserves retirement constraints | Happy | Offline PostgreSQL migration rendering | Retirement constraints appear in generated SQL | Integration | ✅ `backend/tests/integration/db/migrations/test_storage_retirement.py::TestStorageRetirementUpgrade::test_offline_postgres_render_preserves_retirement_constraints` |

## Scratch custody and cleanup

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 35 | records ownership before first byte | Happy | New disposable workspace | Custody and charge exist before payload write | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestScratchWindows::test_records_ownership_before_first_byte` |
| 36 | retains failed cleanup for replay | Error | Exact cleanup fails | Payload and durable receipt remain replayable | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestScratchWindows::test_retains_failed_cleanup_for_replay` |
| 37 | preserves replaced directory | Error | Workspace directory replaced | Foreign replacement preserved | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestScratchWindows::test_preserves_replaced_directory` |
| 38 | defers recovery while writer is active | Edge | Exact writer lock is live | Bytes and credit retained until writer releases | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestScratchWindows::test_defers_recovery_while_writer_is_active` |
| 39 | unproven lock never holds capacity | Error | Preparation left an unproven lock | Unproven debris cannot retain active capacity | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestPreparationRecovery::test_unproven_lock_never_holds_capacity` |
| 40 | replays directory identity commit failure | Error | Directory identity commit fails | Preparation can be recovered without losing proof | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestPreparationRecovery::test_replays_directory_identity_commit_failure` |
| 41 | preserves body exception when cleanup database fails | Error | Body fails; cleanup SQL also fails | Original body exception preserved | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestSQLCleanupFailure::test_preserves_body_exception_when_cleanup_database_fails` |
| 42 | cleanup database failure retains durable claim | Error | Cleanup SQL unavailable | Durable custody and capacity retained | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestSQLCleanupFailure::test_cleanup_database_failure_retains_durable_claim` |
| 43 | preserves replaced sealed output | Error | Sealed output replaced by another inode | Foreign output preserved | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestSealedCustody::test_preserves_replaced_sealed_output` |
| 44 | rejects release of unrelated workspace child | Error | Caller attempts to release another child | Unrelated payload is not authorized for release | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestSealedCustody::test_rejects_release_of_unrelated_workspace_child` |
| 45 | lease commit protects output before handoff | Edge | Input lease commits before custody handoff completes | Leased exact output survives recovery | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestSealedCustody::test_lease_commit_protects_output_before_handoff` |
| 46 | handoff keeps capacity until input lease retires | Edge | Transferred output still has input lease | Charge remains until lease retires | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestSealedCustody::test_handoff_keeps_capacity_until_input_lease_retires` |
| 47 | releases previous epoch before admitting new window | Edge | Prior execution left abandoned workspace | Old exact bytes removed before new admission | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestJobReplayAdmission::test_releases_previous_epoch_before_admitting_new_window` |
| 48 | blocks new epoch while previous bytes cannot be released | Error | Prior epoch cleanup fails | Next epoch cannot accumulate another window | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestJobReplayAdmission::test_blocks_new_epoch_while_previous_bytes_cannot_be_released` |
| 49 | rejects more than 64 prior windows | Error | Prior-window recovery exceeds bounded limit | Explicit refusal prevents unbounded recovery | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestRecoveryBounds::test_rejects_more_than_64_prior_windows` |
| 50 | preserves nested windows in the same epoch | Edge | Nested workspaces share execution epoch | Current nested windows remain valid | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestRecoveryBounds::test_preserves_nested_windows_in_the_same_epoch` |

## Scratch Work Source

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 51 | discovers due windows in bounded order | Happy | Due and future windows; finite discovery limit | Only due oldest windows discovered in stable bounded order | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestScratchCleanupSource::test_discovers_due_windows_in_bounded_order` |
| 52 | reclaims abandoned window through definition | Happy | Abandoned owned window with payload | Cleanup definition removes payload, receipt and credit | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestScratchCleanupSource::test_reclaims_abandoned_window_through_definition` |
| 53 | delays recovery of an active writer | Edge | Due window has active writer | Recovery defers and retains custody | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestScratchCleanupSource::test_delays_recovery_of_an_active_writer` |
| 54 | preserves replacement during background recovery | Error | Owned workspace was replaced | Background cleanup preserves replacement and evidence | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestScratchCleanupSource::test_preserves_replacement_during_background_recovery` |

## URL download custody

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 55 | cleanup preserves sealed archive during resolution | Edge | Real download; cleanup runs during inspection while writer FD remains live | Download and credit survive inspection; final Inbox archive is intact; disposable custody releases | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_jobs.py::TestResolveStep::test_cleanup_preserves_sealed_archive_during_resolution` |
| 56 | retry reclaims prior download before new resolution | Edge | Previous execution left a charged download | Previous bytes and credit disappear before the next GET; only current custody is admitted | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_jobs.py::TestResolveStep::test_retry_reclaims_prior_download_before_new_resolution` |
| 57 | cleanup preserves sealed archive until input handoff | Edge | Cleanup runs during inspection before input lease commits | Exact ZIP survives inspection and handoff; committed lease defers scratch cleanup | Integration | ✅ `backend/tests/integration/modules/ingestion/test_background.py::TestSingleUrlCustody::test_cleanup_preserves_sealed_archive_until_input_handoff` |

## Scratch migration

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 58 | preserves previous work rows | Happy | Existing Jobs and work contracts | Upgrade preserves existing work rows | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchUpgrade::test_preserves_previous_work_rows` |
| 59 | roundtrip preserves previous work | Happy | Populated prior work schema | Upgrade/downgrade preserves prior work | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchUpgrade::test_roundtrip_preserves_previous_work` |
| 60 | accepts cleanup definition in each existing contract | Happy | Each closed Job-definition contract | Scratch cleanup definition accepted | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchUpgrade::test_accepts_cleanup_definition_in_each_existing_contract` |
| 61 | rejects unknown job definitions | Error | Unknown definition in work tables | Closed-set constraints reject unknown values | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchUpgrade::test_rejects_unknown_job_definitions` |
| 62 | offline postgres upgrade renders custody contract | Happy | Offline PostgreSQL upgrade | Custody tables and contracts rendered | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchUpgrade::test_offline_postgres_upgrade_renders_custody_contract` |
| 63 | receipt blocks destructive downgrade | Error | Live scratch receipt exists | Downgrade refuses custody loss | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchCustody::test_receipt_blocks_destructive_downgrade` |
| 64 | retains origin when pruned job unlinks | Edge | Origin Job is pruned | Historical origin remains after FK unlink | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchCustody::test_retains_origin_when_pruned_job_unlinks` |
| 65 | rejects invalid custody evidence | Error | Invalid lock/output/owner/phase combinations | Database refuses invalid custody states | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchCustody::test_rejects_invalid_custody_evidence` |
| 66 | rejects duplicate custody claims | Error | Duplicate custody evidence | Uniqueness constraints reject conflicting claims | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchCustody::test_rejects_duplicate_custody_claims` |
| 67 | removes only scratch cleanup intent | Happy | Scratch and unrelated work exist before downgrade | Only scratch cleanup intent removed | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchDowngrade::test_removes_only_scratch_cleanup_intent` |
| 68 | restores previous closed definition set | Happy | Downgrade completes without live custody | Previous closed definition sets restored | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchDowngrade::test_restores_previous_closed_definition_set` |
| 69 | refuses offline downgrade without custody verification | Error | Offline downgrade cannot verify live custody | Downgrade fails explicitly | Integration | ✅ `backend/tests/integration/db/migrations/test_scratch_windows.py::TestScratchDowngrade::test_refuses_offline_downgrade_without_custody_verification` |

## Source-cover candidates and late compensation

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 70 | pointer and owned receipt commit together | Happy | Create or replacement candidate | Pointer and owned receipt commit atomically | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestCandidatePublication::test_commits_cover_publication_atomically` |
| 71 | rolled back pointer keeps old cover and receipt | Error | Candidate pointer transaction rolls back | Old cover and receipt remain intact | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestCandidatePublication::test_rolled_back_publication_preserves_previous_cover` |
| 72 | later pointer wins and rejected candidate stays private | Edge | Another pointer wins after preparation | Rejected candidate cannot replace winner | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestCandidatePublication::test_rejects_a_candidate_superseded_by_a_later_pointer` |
| 73 | replacement rollback does not restore a concurrently deleted cover | Edge | Real SQLite deletion commits during legacy compensation | Deleted owner cannot resurrect as COMMITTED orphan | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestCommittedCoverCleanup::test_replacement_rollback_does_not_restore_a_concurrently_deleted_cover` |
| 74 | delete preserves a pointer changed after storage preflight | Edge | Cover pointer changes after deletion preflight | Stale delete preserves replacement pointer | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestCommittedCoverCleanup::test_delete_preserves_a_pointer_changed_after_storage_preflight` |
| 75 | commit failure cleanup cannot undo an adopted cover | Edge | Candidate adopted before compensation | Committed cover survives cleanup | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestCommittedCoverCleanup::test_commit_failure_cleanup_cannot_undo_an_adopted_cover` |
| 76 | absence observed before adoption cannot remove a committed cover | Edge | Old absence probe races cover adoption | Adopted cover and ownership remain | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestAbsentCoverRecovery::test_absence_observed_before_adoption_cannot_remove_a_committed_cover` |
| 77 | persists the publication provider ref after an active switch | Edge | Active provider changes after publication | Receipt retains creation provider identity | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestPut::test_persists_the_publication_provider_ref_after_an_active_switch` |
| 78 | remote legacy receipt fails closed without a storage probe | Error | Remote legacy receipt lacks required identity | No unsafe provider probe or adoption | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestExpirePending::test_remote_legacy_receipt_fails_closed_without_a_storage_probe` |
| 79 | foreign receipt fails closed without a storage probe | Error | Receipt belongs to another provider | Foreign receipt cannot authorize active storage | Integration | ✅ `backend/tests/integration/modules/library/test_source_covers.py::TestExpirePending::test_foreign_receipt_fails_closed_without_a_storage_probe` |

## Source-cover router ownership

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 80 | retires the private candidate when the commit fails | Error | PUT caller commit fails | Only private candidate is retired | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestPutModelSourceCover::test_retires_the_private_candidate_when_the_commit_fails` |
| 81 | preserves the committed cover when the commit acknowledgement is lost | Edge | PUT commits then acknowledgement raises | Cover, bytes and committed receipt preserved | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestPutModelSourceCover::test_preserves_the_committed_cover_when_the_commit_acknowledgement_is_lost` |
| 82 | upload uses thread owned sessions | Happy | Bounded source-cover upload command | Worker owns its SQL sessions | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestCoverCommandOwnership::test_upload_uses_thread_owned_sessions` |
| 83 | keeps health responsive during publication | Edge | Publication storage operation blocks | Health progresses on the API loop | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestCoverCommandOwnership::test_keeps_health_responsive_during_publication` |

## SQL compensation and Artifact publication

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 84 | unknown commit resolution keeps the published blob | Error | Commit acknowledgement cannot be resolved | Published bytes retained for reconciliation | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_unknown_commit_resolution_keeps_the_published_blob` |
| 85 | commit ack loss resolves from a fresh session | Edge | Successful commit followed by lost acknowledgement | Fresh SQL read resolves committed Artifact | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_commit_ack_loss_resolves_from_a_fresh_session` |
| 86 | rolls back precommit bytes on cooperative cancellation | Error | Cancellation before domain commit | Unadopted bytes compensated by exact receipt | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_rolls_back_precommit_bytes_on_cooperative_cancellation` |
| 87 | preserves cancellation when receipt cleanup fails | Error | Cancellation plus physical cleanup failure | Cancellation survives with recoverable cleanup evidence | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_preserves_cancellation_when_receipt_cleanup_fails` |
| 88 | preserves cancellation when publication retirement is unavailable | Error | Cancellation plus retirement SQL failure | Original cancellation preserved; proof not fabricated | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_preserves_cancellation_when_publication_retirement_is_unavailable` |
| 89 | preserves committed artifact after cooperative cancellation | Edge | Cancellation after Artifact commits | Committed Artifact and original bytes survive | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestMetadata::test_preserves_committed_artifact_after_cooperative_cancellation` |
| 90 | late upload completion preserves retried staging | Edge | Old upload completes after immediate retry | New attempt retains staging custody | Integration | ✅ `backend/tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py::TestStagedAttemptPublication::test_late_upload_completion_preserves_retried_staging` |

## Backup receipt cleanup

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 91 | defers unversioned receipt when recreation can share its etag | Error | Unversioned remote object may be recreated identically | Cleanup defers rather than deleting by ambiguous ETag | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceipt::test_defers_unversioned_receipt_when_recreation_can_share_its_etag` |
| 92 | deletes only captured version when current generation changes | Edge | Current remote generation changes | Captured version cleanup leaves newer generation intact | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceipt::test_deletes_only_captured_version_when_current_generation_changes` |

## Backup cache and restore consumers

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 93 | noncommitted receipt keeps its ledger classification | Edge | Receipt is pending/retired rather than committed | Verification retains ledger classification | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestVerifyBackupOwnership::test_noncommitted_receipt_keeps_its_ledger_classification` |
| 94 | nonbackup receipt is never treated as an archive | Error | Owned receipt is not a backup object | Receipt cannot be used as backup authority | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestVerifyBackupOwnership::test_nonbackup_receipt_is_never_treated_as_an_archive` |
| 95 | cleanup removes only the owned cloud cache | Happy | Owned cloud backup cache plus unrelated bytes | Only exact owned cache removed | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestDeleteBackup::test_cleanup_removes_only_the_owned_cloud_cache` |
| 96 | cleanup keeps a cache pinned by restore journal | Edge | Restore journal pins cache | Pinned cache survives cleanup | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestDeleteBackup::test_cleanup_keeps_a_cache_pinned_by_restore_journal` |
| 97 | startup cache cleanup retires only exact stale owned cache | Happy | Stale cache receipts on startup | Only exact stale owned cache retired | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestBackupCacheRecovery::test_startup_cache_cleanup_retires_only_exact_stale_owned_cache` |
| 98 | pending cache with exact proof becomes committed | Happy | Pending cache with exact physical proof | Cache adopted with committed authority | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestBackupCacheRecovery::test_pending_cache_with_exact_proof_becomes_committed` |
| 99 | stale cache delete failure preserves bytes for recovery | Error | Exact stale-cache deletion fails | Bytes and proof remain recoverable | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestBackupCacheRecovery::test_stale_cache_delete_failure_preserves_bytes_for_recovery` |
| 100 | ownership queries keep provider namespaces separate | Edge | Equal cache keys across providers | Ownership remains scoped to exact provider | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestBackupCacheRecovery::test_ownership_queries_keep_provider_namespaces_separate` |
| 101 | unresolved restore pins cache with both receipts | Edge | Restore retains ambiguous original/new receipts | Both custody proofs keep cache pinned | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestBackupCacheRecovery::test_unresolved_restore_pins_cache_with_both_receipts` |
| 102 | matching unowned cache collision is preserved | Error | Unowned collision has matching bytes | Matching content does not invent ownership | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestBackupCacheRecovery::test_matching_unowned_cache_collision_is_preserved` |
| 103 | sync restored ownership preserves exact provider identity | Happy | Restored database contains provider-specific receipts | Current receipt restored; retired generation and foreign-provider sibling preserved | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestRestoreDatabase::test_sync_restored_ownership_preserves_exact_provider_identity` |

## Capture and source-merge consumers

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 104 | persists the publication provider ref before an active switch | Edge | Provider changes after slot publication | Slot receipt remains bound to publication provider | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestUploadCaptureSlot::test_persists_the_publication_provider_ref_before_an_active_switch` |
| 105 | capture cover attaches before raw slot receipt is released | Happy | Raw uploaded cover becomes source-cover candidate | Cover adopts before raw custody is released | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestUploadCaptureSlot::test_capture_cover_attaches_before_raw_slot_receipt_is_released` |
| 106 | reconcile finished capture runs normal terminalization | Edge | Restart observes completed capture import with transferred origin leases | Source cover has decoded WebP bytes and matching COMMITTED receipt; terminal cleanup releases capture slots and leases | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_inbox.py::TestReconcileInterruptedItems::test_reconcile_finished_capture_runs_normal_terminalization` |
| 107 | finished capture retires only the candidate when commit fails | Error | Finished capture commit fails | Only unadopted candidate retired | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestCleanupCaptureSlots::test_finished_capture_retires_only_the_candidate_when_commit_fails` |
| 108 | terminal cleanup attaches durable receipt without provider or spool io | Happy | Terminal slot has complete durable publication receipt | Cleanup transfers SQL authority without provider/spool I/O | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestTerminalCaptureCleanup::test_terminal_cleanup_attaches_durable_receipt_without_provider_or_spool_io` |
| 109 | terminal cleanup defers incomplete publication without losing staging owner | Error | Terminal slot publication proof incomplete | Cleanup defers while original staging owner survives | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestTerminalCaptureCleanup::test_terminal_cleanup_defers_incomplete_publication_without_losing_staging_owner` |

## Source merge retirement

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 110 | source merge enqueues exact obsolete cover receipt when target has cover | Edge | Merged sources both have covers | Exact obsolete cover receipt enters deletion outbox | Integration | ✅ `backend/tests/integration/modules/library/provenance/test_captures.py::TestUpsertCapture::test_source_merge_enqueues_exact_obsolete_cover_receipt_when_target_has_cover` |
| 111 | source merge cover delete proof failure rolls back the whole merge | Error | Obsolete-cover deletion proof cannot be authorized | Whole source merge rolls back | Integration | ✅ `backend/tests/integration/modules/library/provenance/test_captures.py::TestUpsertCapture::test_source_merge_cover_delete_proof_failure_rolls_back_the_whole_merge` |

## Derivative consumer fences

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 112 | cancelled thumbnail keeps unpublished bytes receipted | Error | Thumbnail cancelled before adoption | Pending ownership retained; no current thumbnail pointer | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestAttemptPublication::test_cancelled_thumbnail_keeps_unpublished_bytes_receipted` |
| 113 | source changed during publication cannot become ready | Edge | Source changes during viewer publication | Old output cannot become READY | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestDeriveViewerStl::test_source_changed_during_publication_cannot_become_ready` |
| 114 | cancelled conversion retains only pending ownership | Error | Viewer conversion cancelled before adoption | Only pending storage ownership remains | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestDeriveViewerStl::test_cancelled_conversion_retains_only_pending_ownership` |
| 115 | superseded conversion preserves the new viewer bytes | Edge | Old conversion returns after newer viewer commits | New viewer bytes and pointer survive | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestDeriveViewerStl::test_superseded_conversion_preserves_the_new_viewer_bytes` |
| 116 | superseded failure preserves the new ready viewer | Edge | Old conversion failure arrives after newer READY output | New READY state cannot be downgraded | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestDeriveViewerStl::test_superseded_failure_preserves_the_new_ready_viewer` |

## Process-level interruption

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 117 | restarted cleanup job retires interrupted payload | Error | Live cleanup defers writer; SIGKILL prevents finally hooks | Fresh DBOS process removes abandoned payload, custody and credit | E2E | ✅ `backend/tests/e2e/test_ingestion_scratch_recovery.py::TestScratchCrashRecovery::test_restarted_cleanup_job_retires_interrupted_payload` |

## Batch lifecycle E2E

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 118 | cancels before consuming the next entry | Error | Public cancel after first real commit gate | First Artifact intact; no next payload expansion | E2E | ✅ `backend/tests/e2e/test_ingestion_batch_recovery.py::TestBatchInterruption::test_cancels_before_consuming_the_next_entry` |
| 119 | recovers a batch after process death | Error | SIGKILL after first commit; fresh app restarts | Batch converges without duplicate Artifact; first ID preserved | E2E | ✅ `backend/tests/e2e/test_ingestion_batch_recovery.py::TestBatchInterruption::test_recovers_a_batch_after_process_death` |
| 120 | bounds peak staging throughout the batch | Edge | Four large entries exceed one-entry staging window | Distinct-inode sampled peak stays within retained input plus window; logical path totals and allocation snapshots remain diagnostic | E2E | ✅ `backend/tests/e2e/test_ingestion_batch_recovery.py::TestBatchInterruption::test_bounds_peak_staging_throughout_the_batch` |
| 121 | viewer retry recovers after native worker death | Error | Actual native worker is stopped, observed with admission/preparation tickets, then SIGKILLed | Public retry produces a valid watertight viewer; original survives; failed Job settles and exact temporary resources release | E2E | ✅ `backend/tests/e2e/test_viewer_worker_recovery.py::TestViewerWorkerRecovery::test_viewer_retry_recovers_after_native_worker_death` |

## Publication primitives used by shared consumers

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 122 | fresh session resolves a committed artifact after ack failure | Edge | Artifact commit succeeds but acknowledgement fails | Independent SQL read resolves committed owner | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishBytes::test_fresh_session_resolves_a_committed_artifact_after_ack_failure` |
| 123 | publishes every managed key kind through the ledger | Happy | Every managed key kind | Physical publication has durable ownership receipt | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishBytes::test_publishes_every_managed_key_kind_through_the_ledger` |
| 124 | reserves the key durably before storage publication | Happy | Storage creation begins | Reservation already survives independent SQL read | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishBytes::test_reserves_the_key_durably_before_storage_publication` |
| 125 | commits ownership with the callers transaction | Happy | Caller transaction adopts publication | Ownership commits atomically with domain state | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishBytes::test_commits_ownership_with_the_callers_transaction` |
| 126 | keeps pending intent when the domain transaction rolls back | Error | Domain adoption rolls back | Pending receipt remains for recovery | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishBytes::test_keeps_pending_intent_when_the_domain_transaction_rolls_back` |
| 127 | flushed caller dml rolls back after publication | Error | Caller already flushed unrelated DML | Publication does not commit unrelated caller changes | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishBytes::test_flushed_caller_dml_rolls_back_after_publication` |
| 128 | publishes stream content with supplied hash | Happy | Stream with declared size/hash | Stored content and receipt match supplied evidence | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishStream::test_publishes_stream_content_with_supplied_hash` |
| 129 | leaves a pending intent when stream publication fails | Error | Stream storage fails | Pending publication remains discoverable | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishStream::test_leaves_a_pending_intent_when_stream_publication_fails` |
| 130 | publishes a staged file without removing the source | Happy | Publish file without move ownership | Published bytes correct; source preserved | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishFile::test_publishes_a_staged_file_without_removing_the_source` |
| 131 | moves a staged file into storage when requested | Happy | Publish file with move ownership | Managed object created; source consumed | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishFile::test_moves_a_staged_file_into_storage_when_requested` |
| 132 | publishes the local copy when the store cannot copy server side | Edge | Backend lacks server-side copy | Local copy preserves publication contract | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishFile::test_publishes_the_local_copy_when_the_store_cannot_copy_server_side` |
| 133 | can bind a staged file to a purpose scoped provider | Happy | Purpose-specific storage binding | Publication receipt retains selected provider identity | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestPublishFile::test_can_bind_a_staged_file_to_a_purpose_scoped_provider` |
| 134 | reuses a stale committed locator when bytes are absent | Edge | Stale committed receipt; bytes absent | Fresh reservation can reuse canonical locator | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestReserveCreation::test_reuses_a_stale_committed_locator_when_bytes_are_absent` |
| 135 | rejects completion when the reservation is missing | Error | Reservation disappeared before completion | Completion cannot fabricate adopted authority | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_publication.py::TestReserveCreation::test_rejects_completion_when_the_reservation_is_missing` |

## Orphan reclamation safeguards

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 136 | leaves stale backup publication for backup reconciler | Edge | Stale backup pending receipt | Generic sweep leaves backup-specific reconciliation intact | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_leaves_stale_backup_publication_for_backup_reconciler` |
| 137 | ignores a fresh pending reservation | Happy | Fresh publication in progress | Sweep does not retire active work | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_ignores_a_fresh_pending_reservation` |
| 138 | retires and defers a stale reservation when the object is absent | Edge | Expired intent; physical object absent | Authority retired while late-creation evidence remains | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_defers_physical_cleanup_for_an_absent_stale_reservation` |
| 139 | reclaims a matching small orphan | Happy | Small orphan matches exact proof | Only matching owned object reclaimed | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_reclaims_a_matching_small_orphan` |
| 140 | defers an orphan with mismatched evidence | Error | Physical object differs from receipt | Cleanup defers without deleting replacement | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_defers_an_orphan_with_mismatched_evidence` |
| 141 | preserves a same size replacement before hash reclamation | Edge | Replacement has equal size but different identity/content | Hash-assisted reclamation preserves replacement | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_preserves_a_same_size_replacement_before_hash_reclamation` |
| 142 | defers a large orphan without sufficient proof | Error | Large object lacks safe exact evidence | Sweep does not guess ownership | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_defers_a_large_orphan_without_sufficient_proof` |
| 143 | retries a transient reclaim failure | Error | Temporary provider reclaim failure | Durable intent remains retryable | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_retries_a_transient_reclaim_failure` |
| 144 | never sweeps committed ownership | Happy | Committed domain owner | Sweep leaves current ownership intact | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_never_sweeps_committed_ownership` |
| 145 | blocks a pending reservation for another backend | Error | Pending receipt belongs to another backend | Current backend cannot reclaim foreign object | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_blocks_a_pending_reservation_for_another_backend` |
| 146 | guarded backend never check then deletes an orphan | Edge | Backend needs guarded hash authorization | No unsafe unconditional delete follows probe | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_guarded_backend_never_check_then_deletes_an_orphan` |
| 147 | reclaims a stale versioned reservation | Happy | Stale exact versioned receipt | Only captured generation reclaimed | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_reclaims_a_stale_versioned_reservation` |
| 148 | defers a stale reservation with a size mismatch | Error | Provider reports different size | Mismatch prevents reclamation | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_defers_a_stale_reservation_with_a_size_mismatch` |
| 149 | reclaims a stale reservation with matching etag | Happy | Supported receipt has matching ETag evidence | Exact supported reclaim succeeds | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_reclaims_a_stale_reservation_with_matching_etag` |
| 150 | blocks a stale reservation when reclaim does not remove | Error | Provider reports reclaim did not remove object | Blocked proof retained rather than counted as success | Integration | ✅ `backend/tests/integration/modules/storage/storage_ownership/test_sweep.py::TestSweepOrphanedPublications::test_blocks_a_stale_reservation_when_reclaim_does_not_remove` |

## Deletion outbox provider contracts

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 151 | same key from another provider is not authorized | Error | Same key is owned by another provider | Provider-scoped proof cannot authorize deletion | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_deletion.py::TestEnqueueOwnedKey::test_same_key_from_another_provider_is_not_authorized` |
| 152 | same receipt tuple remains distinct across providers | Edge | Receipt fields coincide across provider bindings | Blocked intents remain independently scoped | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_deletion.py::TestRecordLegacyBlockedIntent::test_same_receipt_tuple_remains_distinct_across_providers` |
| 153 | restart blocks intent when provider destination changed | Edge | Provider destination changes before restart | Old intent blocked without deleting current destination | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_deletion.py::TestProcessStorageDeleteIntents::test_restart_blocks_intent_when_provider_destination_changed` |
| 154 | classifies the exact cleanup result | Happy | Each provider reclamation outcome | Intent classification reflects exact result | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_deletion.py::TestProcessStorageDeleteIntents::test_classifies_the_exact_cleanup_result` |
| 155 | verified intent retries after worker crash | Error | Worker stops after durable verification | Restart retries exact authorized cleanup | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_deletion.py::TestProcessStorageDeleteIntents::test_verified_intent_retries_after_worker_crash` |

## Publication and scratch factories

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 156 | locator factory matches the production exclusion identity | Happy | Factory-created publication locator | Factory key matches production exclusion contract | Repo | ✅ `backend/tests/repo/test_factories.py::TestStoragePublicationFactories::test_locator_factory_matches_the_production_exclusion_identity` |
| 157 | owned factory generates distinct publication authority | Happy | Two factory-created owned objects | Generated authority identities are distinct | Repo | ✅ `backend/tests/repo/test_factories.py::TestStoragePublicationFactories::test_owned_factory_generates_distinct_publication_authority` |
| 158 | defaults to uncharged preparing custody | Happy | Default scratch factory | PREPARING custody has no fabricated charge | Repo | ✅ `backend/tests/repo/test_factories.py::TestBuildIngestionScratchWindow::test_defaults_to_uncharged_preparing_custody` |
| 159 | rejects job owner without execution epoch | Error | Job-owned scratch lacks epoch | Factory rejects invalid ownership arrangement | Repo | ✅ `backend/tests/repo/test_factories.py::TestBuildIngestionScratchWindow::test_rejects_job_owner_without_execution_epoch` |

## Qualification accounting and control gates

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 160 | preserves geometry payload | Happy | Two fresh variants of one binary STL | Geometry payload unchanged; source hashes distinct | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestVariant::test_preserves_geometry_payload` |
| 161 | preserves archive members | Happy | Fresh variant of a 3MF archive | Every member unchanged; source archive untouched | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestVariant::test_preserves_archive_members` |
| 162 | requires both thresholds | Edge | Duration short or distinct count short | Neither threshold alone qualifies soak | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestSummarize::test_requires_both_thresholds` |
| 163 | counts distinct usable artifacts | Edge | Fresh/reused/unverified/refused observations | Only distinct verified usable Artifacts count | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestSummarize::test_counts_distinct_usable_artifacts` |
| 164 | retains every outcome | Happy | Completed, refused, failed and timeout observations | All outcome counts remain visible | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestSummarize::test_retains_every_outcome` |
| 165 | excludes replay that creates a fresh artifact | Error | Broken replay creates another File ID | Replay purpose still excluded from useful count | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestQualificationCredits::test_excludes_replay_that_creates_a_fresh_artifact` |
| 166 | excludes warmup artifacts | Happy | Warmup creates usable Artifact | Warmup excluded from qualification count | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestQualificationCredits::test_excludes_warmup_artifacts` |
| 167 | requires exact refusal | Error | Every terminal outcome for malformed control | Only REFUSED satisfies expected-refusal control | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestExpectedRefusal::test_requires_exact_refusal` |
| 168 | rejects missing heartbeat samples | Error | No ASGI heartbeat observations | Missing heartbeat cannot pass responsiveness gate | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestHeartbeatSummary::test_rejects_missing_heartbeat_samples` |
| 169 | rejects lag at the gate | Edge | Lag at 50ms or above | Strict under-50ms gate fails | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestHeartbeatSummary::test_rejects_lag_at_the_gate` |
| 170 | accepts lag below the gate | Happy | Lag below 50ms | Responsiveness gate passes | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestHeartbeatSummary::test_accepts_lag_below_the_gate` |
| 171 | rejects incomplete archive evidence | Error | Missing batch/source/original/derivative evidence | Archive cannot qualify from incomplete evidence | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestArchiveVerified::test_rejects_incomplete_archive_evidence` |
| 172 | accepts verified terminal sources | Happy | Completed batch; unchanged source; originals and terminal derivatives verified | Archive evidence accepted | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestArchiveVerified::test_accepts_verified_terminal_sources` |
| 173 | excludes preexisting sources | Edge | Source SHA already existed before measurement | Existing source excluded from fresh useful count | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestFreshIdentity::test_excludes_preexisting_sources` |
| 174 | counts duplicate source hash once | Edge | Multiple observations reuse one input SHA | Source credited once | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestFreshIdentity::test_counts_duplicate_source_hash_once` |
| 175 | excludes unverified preview pixels | Error | Original is valid but preview decode unverified | Artifact excluded from usable preview count | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestDecodedPreview::test_excludes_unverified_preview_pixels` |
| 176 | gates p95 while retaining outlier maximum | Edge | Heartbeat sample distribution contains outlier | p95 gates; maximum remains separately visible | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestHeartbeatPercentile::test_gates_p95_while_retaining_outlier_maximum` |
| 177 | observes cpu limits without host count fallback | Edge | CPU affinity/cgroup capacity evidence | Effective CPU capacity reflects actual restrictions | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestEffectiveCPUCapacity::test_observes_cpu_limits_without_host_count_fallback` |
| 178 | gates full flow ratio on a capable host | Happy | Comparable useful throughput on capable host | Full-flow ratio gate applied | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestThroughputSummary::test_gates_full_flow_ratio_on_a_capable_host` |
| 179 | reports uncapable host as unqualified | Error | Host lacks required effective capacity | Host reported unqualified, not a speedup success | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestThroughputSummary::test_reports_uncapable_host_as_unqualified` |
| 180 | rejects missing serial useful throughput | Error | Serial baseline has no useful throughput | Ratio cannot qualify | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestThroughputSummary::test_rejects_missing_serial_useful_throughput` |
| 181 | rejects invalid throughput measurements | Error | Nonfinite/invalid throughput data | Invalid measurements rejected | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestThroughputSummary::test_rejects_invalid_throughput_measurements` |
| 182 | rotates closed geometry at distinct sizes | Happy | Generated soak corpus across sizes | Closed geometry remains meaningful and distinct | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestSoakCorpus::test_rotates_closed_geometry_at_distinct_sizes` |
| 183 | periodic controls have distinct identities | Happy | Repeated malformed control inputs | Control identities distinct rather than reused dedupe | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestMalformedControl::test_periodic_controls_have_distinct_identities` |

## Qualification CLI and archive verification

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 184 | rejects invalid requests | Error | Invalid counts, deadlines, modes, archive and native-slot arguments | CLI rejects before creating output workspace | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_rejects_invalid_requests` |
| 185 | runs a private smoke | Happy | Short isolated real-app smoke; unrelated vault sentinel | Fresh original and decoded derivatives verified; smoke labeled; vault unchanged | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_runs_a_private_smoke` |
| 186 | retains deadline evidence | Error | Overall deadline expires before useful work | Failed report retained rather than replaced by success | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_retains_deadline_evidence` |
| 187 | verifies a reviewed archive | Happy | Real archive review and selection | All originals and expected derivative evidence verified | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_verifies_a_reviewed_archive` |
| 188 | records every load cell | Happy | Short configured LOAD measurement | Every serial/mixed cell recorded; smoke cannot certify full LOAD | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_records_every_load_cell` |
| 189 | completes a verified source | Happy | Exact original and expected ready derivatives | Source counted completed only after thumbnail fetch/decode | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_completes_a_verified_source` |
| 190 | rejects a mismatched original | Error | Downloaded original differs from expected bytes | Source verification fails | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_rejects_a_mismatched_original` |
| 191 | rejects an undecodable thumbnail | Error | Thumbnail HTTP succeeds with invalid image bytes | Source cannot be counted usable | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_rejects_an_undecodable_thumbnail` |
| 192 | rejects an unavailable thumbnail | Error | Expected preview HTTP unavailable | Source verification fails | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_rejects_an_unavailable_thumbnail` |
| 193 | rejects a missing expected derivative | Error | Metadata or thumbnail derivative absent | Source cannot qualify from a partial derivative set | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_rejects_a_missing_expected_derivative` |
| 194 | preserves terminal unavailability | Edge | Required derivative ends refused/failed with explicit reason | Terminal unavailability retained as unavailable evidence | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_preserves_terminal_unavailability` |
| 195 | rejects a missing artifact | Error | Artifact disappeared during verification | Missing original owner fails verification | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestVerifyArchiveArtifact::test_rejects_a_missing_artifact` |

## Shared consumer compatibility

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 196 | stores the uploaded image | Happy | Source exists; valid uploaded image | Source-cover pointer and image are available | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestPutModelSourceCover::test_stores_the_uploaded_image` |
| 197 | normalizes it to webp | Happy | Supported image upload | Stored image normalized to WEBP | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestPutModelSourceCover::test_normalizes_it_to_webp` |
| 198 | replaces a cover that is already there | Edge | Source already has cover | New candidate becomes the current cover | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestPutModelSourceCover::test_replaces_a_cover_that_is_already_there` |
| 199 | refuses bytes that are not an image | Error | Invalid image body | Upload refused without adopting a cover | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestPutModelSourceCover::test_refuses_bytes_that_are_not_an_image` |
| 200 | removes the cover | Happy | Owned source has committed cover | DELETE removes the source-cover pointer | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestDeleteModelSourceCover::test_removes_the_cover` |
| 201 | reports a source with no cover | Edge | Source has no cover | Existing missing-cover response preserved | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestDeleteModelSourceCover::test_reports_a_source_with_no_cover` |
| 202 | rejects a caller who may only view the collection | Error | Caller has view permission only | DELETE denied without removing cover | Integration | ✅ `backend/tests/integration/api/v1/models/test_provenance.py::TestDeleteModelSourceCover::test_rejects_a_caller_who_may_only_view_the_collection` |
| 203 | reconciles a local archive published before its receipt | Edge | Local backup bytes published before receipt commit | Restart adopts the exact recoverable local archive | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestReconcileBackupPublications::test_reconciles_a_local_archive_published_before_its_receipt` |
| 204 | trash purges a pre ledger artifact it can verify | Happy | Upgraded local Artifact predates ownership ledger | Real API purge verifies and removes original bytes | E2E | ✅ `backend/tests/e2e/test_trash_legacy_ownership.py::TestTrash::test_trash_purges_a_pre_ledger_artifact_it_can_verify` |

## Task Center labels

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 205 | titles a discovered scratch cleanup Job with its supplied label | Happy | Running ingestion.scratch_cleanup Job with supplied display label | Task title uses the supplied label rather than generic Import | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::titles a discovered scratch cleanup Job with its supplied label` |
| 206 | titles a discovered archive inspection Job as ZIP preparation | Happy | Running ingestion.archive_inspect Job | Task title is Prepare ZIP | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::titles a discovered archive inspection Job as ZIP preparation` |

The existing import-title contract remains covered by
`frontend/src/lib/__tests__/task-center.test.ts::titles a discovered import Job as an import`.

## Archive adoption rejection with retained history

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 207 | archive adoption rejects an undeclared regular member | Error | Unowned rewritten archive has an undeclared member and retired publication history | Adoption refuses invalid manifest; historical receipts and archive bytes remain unchanged; no committed receipt is fabricated | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_core.py::TestListLocalBackups::test_archive_adoption_rejects_an_undeclared_regular_member` |

Generation-aware assertions select the current COMMITTED receipt rather than
an arbitrary same-key row. Restore additionally checks that the prior current
provider receipt remains RETIRING with its original generation/evidence and the
foreign-provider sibling is unchanged.

## Qualification work deadlines

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 208 | rejects an exhausted work window | Error | Work deadline is exact or expired | Timeout refuses admission after the work window | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestRemainingJobSeconds::test_rejects_an_exhausted_work_window` |
| 209 | caps the Job at remaining work time | Edge | Remaining work window is shorter or longer than Job limit | Job deadline is the smaller remaining window or configured Job bound | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestRemainingJobSeconds::test_caps_the_job_at_remaining_work_time` |

## Confirmed execution evidence

The completed selections below overlap and **must not be added together**.
They establish the named checks, not a full-suite or release qualification.

| Selection | Confirmed result |
|---|---|
| Artifact atomicity, legacy-trash E2E and mesh-boundary invariants | 139 passed |
| Publication races, ownership retirement/publication/sweep and storage-retirement migration | 93 passed |
| Source-cover owner and provenance router mirrors | 84 passed |
| Ingestion background, batch resume/windows/commits, scratch windows and archive windows | 83 passed |
| Real scratch crash-recovery E2E | 1 passed |
| Task Center test file, including discovered-Job labels | 87 passed in 9.41 s |
| Scratch migration suite | 79 passed in 141.57 s |
| Upload cancellation custody and affected ingestion selections | 148 passed in 92.50 s |
| Expanded foundations: scratch custody, Inbox import mirrors, backup receipt cleanup, publication identity and qualification deadline evidence | 197 passed, 11 deselected in 63.26 s |
| Native and harness selection | 46 passed, 1 failed in 311.40 s; its parent-deadline report assertion passed in the subsequent foundations selection |
| Real harness smoke, reviewed archive and tiny LOAD cell matrix | Passed; these are small integration runs |
| Sustained foreground/backfill fairness | Both E2E tests passed |
| Native quality under concurrent demand | All six STL/3MF × 1/2/4-slot variants passed |
| Descendant peak export | Known descendant allocation appeared in exported process-tree peak |
| Batch cancellation, process-death recovery and corrected sampled peak | All three E2E nodes passed |
| Viewer retry after actual native worker death | E2E passed |
| Inbox source custody and Job retention | 17 passed in 24.26 s, including all five active-read/pre-claim cases and all 12 pruning cases |
| Affected consumers, OpenAPI, factories, CI allocation and mesh boundaries | 271 passed, 1 failed in 85.15 s; the corrected completed-item fixture passed in the subsequent focused extraction selection |
| Focused foundations coverage after extraction | 135 passed in 23.29 s; eight provider-binding cases subsequently passed after correcting the configuration fixture |
| Receipt-contract extraction, storage consumers and architecture | 147 passed in 31.02 s; cyclic, private, legacy and transport dependency lists all empty |
| Changed-test structural checks | 288 passed; four single-behavior names corrected, conjunction-name gate then passed |
| Terminal lost-executor recovery (reconciler/bootstrap) | 128 passed in 13.61s |
| Unchanged real SIGKILL scratch recovery | 1 passed in 28.76s |
| High-descriptor readiness and original CI failures | 4 passed in 7.22s |
| Focused namespace repository checks | 5 passed in 13.61s; remote real probe pending |
| Final backend lint and changed-file formatting | Ruff clean; 178 CI-scope/changed Python files formatted |
| Configured backend types, lint and format | Pyright: zero errors/warnings; Ruff clean; 172 CI-scope files formatted |

Fresh measurements on the frozen backend put scratch custody at **93.14% combined
statement/branch coverage** and storage publication at **90.00%**. Backup receipt
cleanup reached **100%**. The extracted provider-receipt contract reached **98.43%** after its
endpoint, configured-backup and transport-option cases. All figures include branches.
No coverage floor was lowered.

A private real-model archive completed in **573.19 seconds** with one native
slot: **85/85** original hashes verified, metadata ready and decoded thumbnails.
Natural drain reached zero active Jobs, reservations, staging leases and optional
fingerprint continuations. Staging, prepared and native temporary payloads were
empty after shutdown. The sampled process-tree RSS peak was 1,270,263,808 bytes
(107 samples); this is a sampled observation, not an instantaneous upper bound.

LOAD completed **100/100 useful Artifacts per cell** at concurrency 1, 2 and 4
in 536.92, 400.21 and 424.14 seconds on source `3e037c2f`. The first concurrency-8
cell failed: eight completed and 92 received HTTP507 because the private
installation retained its default four active staging leases per user. That
failed evidence is retained.

The selected concurrency-8 repeat passed **100/100** on source `5fd919e9` with
an explicit private eight-lease profile; byte, free-space, global and native
limits were unchanged. Its reviewed report records **0.153385894 useful
Artifacts/second**, **8.33ms ASGI event-loop heartbeat p95**, and natural drain
to zero active Jobs, leases, reservations, continuations and temporary payload.
The host admits only **three physical one-GiB workers**, so these measurements
do not qualify the four-worker **2.5×** throughput gate. Heartbeat measures the
application loop, not socket latency. Different source commits and private
profiles are recorded explicitly; no causal throughput improvement is claimed.
The later terminal-engine recovery fix applies only to terminal attempts left
on dead executors; it was not part of those measured source snapshots.

The **7200-second /1000-useful-Artifact soak remains pending**, as do the updated
exact-head CI gates and remote namespace qualification. Supported amd64/arm64
resource CI checks pass; the complete Deep CI gate is still pending.

## Reproduction

Run commands from `backend/`, using the repository's dev environment. Run the
selections serially when collecting process-death and resource evidence.

```sh
uv run pytest -n 0 -q \
  tests/integration/modules/storage/test_storage_publication.py \
  tests/integration/modules/storage/storage_ownership/test_retirement.py \
  tests/integration/modules/storage/storage_ownership/test_publication.py \
  tests/integration/modules/storage/storage_ownership/test_sweep.py \
  tests/integration/db/migrations/test_storage_retirement.py
```

```sh
uv run pytest -n 0 -q \
  tests/integration/modules/ingestion/test_scratch_windows.py \
  tests/integration/modules/ingestion/test_jobs.py \
  tests/integration/modules/ingestion/test_staging_leases.py \
  tests/integration/modules/ingestion/test_staging_cleanup.py \
  tests/integration/db/migrations/test_scratch_windows.py \
  tests/e2e/test_ingestion_scratch_recovery.py \
  tests/e2e/test_ingestion_batch_recovery.py
```

```sh
uv run pytest -n 0 -q \
  tests/integration/modules/library/test_source_covers.py \
  tests/integration/api/v1/models/test_provenance.py \
  tests/integration/modules/ingestion/ingestion/test_ingestion_atomicity.py \
  tests/integration/modules/backups/backup/test_receipt_cleanup.py \
  tests/integration/modules/backups/backup/test_core.py \
  tests/integration/modules/ingestion/inbox/test_capture_slots.py \
  tests/integration/modules/library/provenance/test_captures.py \
  tests/integration/modules/derivatives/test_producers.py \
  tests/integration/modules/storage/test_storage_deletion.py \
  tests/repo/test_factories.py
```

```sh
uv run pytest -n 0 -q \
  tests/unit/scripts/test_qualify_ingestion.py \
  tests/integration/scripts/test_qualify_ingestion.py
```

The qualification CLI creates its own disposable vault and retains reports
separately. Choose fresh output directories. Archive input is read-only.
Examples below are commands to execute, not results already obtained:

```sh
uv run python -m scripts.qualify_ingestion \
  --mode archive --archive /path/to/corpus.zip \
  --output-dir /tmp/printstash-archive-qualification

uv run python -m scripts.qualify_ingestion \
  --mode load --samples 100 --native-slots 4 \
  --output-dir /tmp/printstash-load-qualification

uv run python -m scripts.qualify_ingestion \
  --mode soak --duration-seconds 7200 --min-artifacts 1000 \
  --deadline-seconds 9000 --drain-seconds 300 \
  --output-dir /tmp/printstash-soak-qualification
```

LOAD defaults to cells 1/2/4/8. Use `--load-concurrency 8` to repeat only that
cell; `--load-concurrency 4 1` retains the requested measurement order. The report
records the effective private admission profile. A subset lacking cell 1 or 4
reports scaling as unassessed, rather than substituting rates from another run.

Archive acceptance requires a completed batch, unchanged source archive,
verified original downloads and both expected derivative outcomes. A completed
preview additionally requires successful HTTP fetch and image decode; refusal
remains an explicit unavailable outcome. Fresh useful counts exclude preexisting
source hashes, duplicates, replay and warmup. Malformed controls require exact
refusal. LOAD retains every cell and gates actual ASGI-loop heartbeat p95 below
50 ms, with the maximum retained separately. Throughput ratios require usable
serial evidence and a host capable of the requested comparison.

Resource observations distinguish warmup from measured work and natural drain
from forced teardown. Failed evidence is retained rather than converted to a
passing result. Cancellation can finish before the cancel request wins; such an
honest completion conflict is reported without claiming cancellation was proven.

The batch peak test samples every 2 ms and at SQL commit boundaries throughout
selection. It also audits actual expanded payload opens, so cancellation cannot
pass merely because later files were quickly removed. Its peak is an **observed
sampled maximum**, not a mathematical bound on every instant between samples.
The retained source archive is charged as a baseline; the asserted additional
staging bound is the disposable one-entry window. Each observed `(device, inode)`
is charged once using its largest observed size. Logical pathname sums remain
separate because a non-atomic scan can visit an archive before and after its
quarantine rename, or through hardlink aliases. Distinct inodes are fully charged;
this does not relax the byte bound. The helper retains physical extent, allocated
block and logical peaks with paths, sizes, link counts and identities in the
fixture's `batch.observation.json` for diagnosis after a green run.

The viewer E2E observes the actual `stl_worker` command and stopped OS process,
then kills that worker. Public refusal and retry must settle the failed attempt,
release its admission/preparation custody and produce a valid viewer while
preserving the original. This single interruption scenario does not certify
native throughput or long-running resource stability.

Offline PostgreSQL migration rendering proves SQL shape, not live PostgreSQL
concurrency. Fake remote receipt tests prove the provider contract exercised by
the fake; supported-provider service runs remain separate. See also
[bounded batch imports](bounded-batch-imports.md),
[native resource admission](native-resource-admission.md),
[mesh cancellation](mesh-cancellation.md) and
[mesh regression](mesh-regression.md).

Frontend label reproduction, from `frontend/`:

```sh
pnpm test src/lib/__tests__/task-center.test.ts
```

## CI allocation and focused remaining checks

Static inspection maps each scoped test file to one existing PR shard:

| Files under `backend/tests/` | PR shard |
|---|---|
| `e2e/test_ingestion_batch_recovery.py`, `e2e/test_ingestion_scratch_recovery.py`, `e2e/test_viewer_worker_recovery.py` | `e2e` |
| `integration/modules/ingestion/`, `integration/modules/library/`, `integration/modules/backups/`, `integration/modules/derivatives/` | `integration-domains-a` |
| `integration/modules/storage/` | `integration-domains-b` |
| `integration/api/v1/models/` | `integration-api-domains` |
| `integration/db/migrations/test_scratch_windows.py`, `integration/db/migrations/test_storage_retirement.py` | `integration-migrations-c` (`test_[n-z]*.py`) |
| `unit/scripts/test_qualify_ingestion.py` | `unit` |
| `integration/scripts/test_qualify_ingestion.py` | `integration-platform` |
| `repo/test_factories.py` and repository hygiene/CI invariants | `repository` |

Process roles in `tests/fakes/` are support modules exercised by their E2Es;
they are not independently collected test files. Script tests mirror the shipped
`backend/scripts/qualify_ingestion.py`. New tests retain grouped classes and
contract headers, without importing another `test_` module. These are static
observations; the invariant tests below remain the executable gate.

From `backend/`, run the remaining focused selections serially:

```sh
uv run pytest -n 0 -q \
  tests/integration/modules/ingestion/inbox/test_jobs.py::TestResolveStep \
  tests/integration/modules/ingestion/test_background.py::TestSingleUrlCustody \
  tests/integration/modules/ingestion/inbox/test_inbox.py::TestReconcileInterruptedItems::test_reconcile_finished_capture_runs_normal_terminalization \
  tests/e2e/test_ingestion_batch_recovery.py \
  tests/e2e/test_viewer_worker_recovery.py \
  tests/e2e/test_ingestion_fairness.py

uv run pytest -n 0 -q \
  tests/repo/test_test_hygiene.py \
  tests/repo/test_ci_workflows.py::TestQuickGate::test_backend_shards_cover_every_test_file_once \
  tests/repo/test_factories.py

uv run ruff check scripts/qualify_ingestion.py tests/fakes/ingestion_batch_process.py \
  tests/fakes/ingestion_scratch_process.py tests/fakes/viewer_recovery_process.py
```

PR CI runs Ruff over `app/ tests/`; that command does not include `scripts/`.
The default Pyright include list also does not select these new scripts or
process helpers, so use explicit scoped type checks where relevant. Repository
invariants and focused runs do not replace supported-provider service contracts,
Deep CI coverage, full corpus verification, measured LOAD or the two-hour soak.

## Exact scratch receipt recovery

These rows are included in the expanded foundations result above. Existing
URL/background custody tests accompany the direct scratch selection when
measuring the owner. No floor was lowered.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 210 | uncertain custody preserves payload | Error | Parent, lock or marker token/type/size/absence changes | Cleanup refuses; payload and durable credit survive | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestReceiptIntegrity::test_uncertain_custody_preserves_payload` |
| 211 | sealing rejects nonregular output | Error | Output is a symlink or directory | Sealing refuses; foreign bytes remain intact | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestReceiptIntegrity::test_sealing_rejects_nonregular_output` |
| 212 | sealing rejects output outside custody | Error | Output belongs outside the workspace | Sealing refuses without touching foreign bytes | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestReceiptIntegrity::test_sealing_rejects_output_outside_custody` |
| 213 | replays half finished retirement | Error | Failure before empty directory removal or after actual lock unlink | Durable credit remains; replay finishes exact retirement idempotently | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestInterruptedRetirement::test_replays_half_finished_retirement` |
| 214 | unowned path is preserved | Edge | Foreign path has no scratch receipt | Release reports NOT_OWNED; handoff leaves bytes intact | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestPathRelease::test_unowned_path_is_preserved` |
| 215 | live writer defers path release | Edge | Sealed output still has live writer FD | Release reports DEFERRED; output and credit remain | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestPathRelease::test_live_writer_defers_path_release` |
| 216 | detached output releases exact custody | Happy | Sealed detached output has no input lease | Release reports RELEASED; exact workspace and credit disappear | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestPathRelease::test_detached_output_releases_exact_custody` |
| 217 | unproven lock cannot release output | Error | Lock is absent, replaced or a symlink | Release reports UNCERTAIN; output and credit remain | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestPathRelease::test_unproven_lock_cannot_release_output` |
| 218 | handoff requires committed input lease | Error | Live or detached sealed output has no committed lease | Handoff refuses; bytes and credit remain recoverable | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestInputHandoff::test_handoff_requires_committed_input_lease` |
| 219 | detached handoff keeps exact input lease | Happy | Detached sealed output has exact committed input lease | TRANSFERRED custody retains lease, output and credit until lease retirement | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestInputHandoff::test_detached_handoff_keeps_exact_input_lease` |
| 220 | refuses invalid window limit | Error | Zero, negative, boolean or fractional byte limit | Admission refuses before creating custody or capacity credit | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestAdmissionValidation::test_refuses_invalid_window_limit` |
| 221 | refuses incomplete job owner | Error | Missing Job id or execution epoch | Owner construction refuses incomplete authority | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestAdmissionValidation::test_refuses_incomplete_job_owner` |
| 222 | refuses empty request token | Error | Empty request token | Owner construction refuses incomplete authority | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestAdmissionValidation::test_refuses_empty_request_token` |
| 223 | request custody records supplied token | Happy | Valid request token | Receipt records token without Job authority | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestAdmissionValidation::test_request_custody_records_supplied_token` |
| 224 | handoff rejects a replaced leased input | Error | Sealed replacement inode differs from committed lease | Handoff refuses; lease, original bytes, replacement and credit survive | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestLeaseIdentity::test_handoff_rejects_a_replaced_leased_input` |
| 225 | retry admission preserves committed prior input | Edge | Prior execution has an exact committed input lease | New admission preserves prior bytes, lease and charge | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestLeaseIdentity::test_retry_admission_preserves_committed_prior_input` |
| 226 | refuses untrusted workspace parent | Error | Workspace parent is public or a symlink | Admission refuses before custody or capacity records appear | Integration | ✅ `backend/tests/integration/modules/ingestion/test_scratch_windows.py::TestParentAdmission::test_refuses_untrusted_workspace_parent` |

## Input custody and exact provider receipts

Parameter variants share a row. Runtime selections are listed separately above.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 227 | captures immutable reservation identity | Happy | Reservation row later changes | Captured generation/provider/locator authority stays immutable | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestPublicationReservation::test_captures_immutable_reservation_identity` |
| 228 | refuses missing reservation identity | Error | Missing row id or generation | Identity construction refuses invalid authority | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestPublicationReservation::test_refuses_missing_reservation_identity` |
| 229 | accepts historical local token alias | Edge | Same physical local identity; historical token differs | Physical generation matches in both comparison directions | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_accepts_historical_local_token_alias` |
| 230 | refuses a different local physical generation | Error | Backend, namespace, key, size, device, inode or ctime differs | Different physical generation cannot match | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_refuses_a_different_local_physical_generation` |
| 231 | accepts remote version with historical logical token | Edge | Same immutable remote version; logical token differs | Immutable version matches despite historical token alias | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_accepts_remote_version_with_historical_logical_token` |
| 232 | refuses same etag with another remote version | Error | Equal ETag; different immutable versions | Remote physical generations remain distinct | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_refuses_same_etag_with_another_remote_version` |
| 233 | accepts exact unversioned remote receipt | Happy | Missing, empty or null version with otherwise exact evidence | Exact receipt matches itself | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_accepts_exact_unversioned_remote_receipt` |
| 234 | refuses unversioned remote token alias | Error | Unversioned remote receipt changes logical token | Token alias cannot establish physical identity | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_refuses_unversioned_remote_token_alias` |
| 235 | uses exact evidence when local stat identity is missing | Edge | Legacy local receipt lacks stat identity | Exact fallback matches; changed token refuses | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_publication.py::TestSameCreation::test_uses_exact_evidence_when_local_stat_identity_is_missing` |
| 236 | preserves receipt when s3 provider is unavailable | Error | Saved provider cannot be resolved | Cleanup raises; captured version evidence remains | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_preserves_receipt_when_s3_provider_is_unavailable` |
| 237 | refuses changed s3 binding | Error | Bucket or provider differs | PROVIDER_MISMATCH without provider I/O | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_refuses_changed_s3_binding` |
| 238 | reconstructs an empty target bucket from saved receipt | Edge | Target lacks bucket; exact saved receipt identifies it | Only saved immutable version is deleted; replacement survives | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_reconstructs_an_empty_target_bucket_from_saved_receipt` |
| 239 | accepts an already absent s3 generation | Edge | Provider returns recognized missing-generation errors | ABSENT terminal cleanup outcome | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_accepts_an_already_absent_s3_generation` |
| 240 | reports s3 generation mismatch | Error | Provider reports precondition/412 failure | MISMATCH outcome retains distinction from absence | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_reports_s3_generation_mismatch` |
| 241 | propagates unknown s3 failure | Error | Provider denies exact deletion | Provider error propagates instead of claiming removal | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_propagates_unknown_s3_failure` |
| 242 | refuses null s3 version | Error | Mutable null remote version | UNSUPPORTED; no exact deletion attempted | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_refuses_null_s3_version` |
| 243 | rejects an unknown backend | Error | Unknown receipt backend | Explicit unsupported-backend error | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptProviderSafety::test_rejects_an_unknown_backend` |
| 244 | deletes only the saved physical version | Happy | Saved immutable version plus replacement | Only saved version disappears | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptOpenDAL::test_deletes_only_the_saved_physical_version` |
| 245 | accepts an already absent exact version | Edge | Saved version is already absent | Cleanup completes; replacement remains | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptOpenDAL::test_accepts_an_already_absent_exact_version` |
| 246 | refuses a receipt without immutable version | Error | Missing/null version | UNSUPPORTED; all versions remain | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptOpenDAL::test_refuses_a_receipt_without_immutable_version` |
| 247 | refuses a transport without exact deletion | Error | Transport lacks exact-deletion capability | UNSUPPORTED; all versions remain | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptOpenDAL::test_refuses_a_transport_without_exact_deletion` |
| 248 | preserves versions when exact delete is refused | Error | Provider refuses versioned deletion | UNSUPPORTED; all versions remain | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptOpenDAL::test_preserves_versions_when_exact_delete_is_refused` |
| 249 | preserves versions when saved destination is missing | Error | Provider or namespace no longer resolves | Cleanup raises; both physical versions remain | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_receipt_cleanup.py::TestReclaimReceiptOpenDAL::test_preserves_versions_when_saved_destination_is_missing` |
| 250 | cleanup fault preserves primary actor error | Error | Actor fails and settled replay SQL also fails | Original actor exception survives with cleanup diagnostic | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestJobInput::test_cleanup_fault_preserves_primary_actor_error` |
| 251 | input rejects invalid receipt identity | Error | Device, inode, ctime or size proof differs | Input custody refuses without modifying bytes | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestJobInput::test_input_rejects_invalid_receipt_identity` |
| 252 | input rejects replacement after receipt read | Error | Regular, symlink or directory replaces prepared path | Custody refuses replacement; foreign bytes survive | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestJobInput::test_input_rejects_replacement_after_receipt_read` |
| 253 | retry actor waits for previous input owner | Edge | Old actor remains active across cancel/retry | New actor waits, then consumes preserved exact input | Integration | ✅ `backend/tests/integration/modules/ingestion/test_jobs.py::TestJobInput::test_retry_actor_waits_for_previous_input_owner` |
| 254 | cancel preserves input until upload actor stops | Edge | Upload paused inside actual persistence; cancel wins | Input and lease survive live actor; exact cleanup follows actor exit | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_cleanup.py::TestCancellationCustody::test_cancel_preserves_input_until_upload_actor_stops` |
| 255 | reconciliation reclaims cancelled input after actor sigkill | Error | OS process holding cancelled input is killed | Reconciliation reclaims exact input and lease after kernel unlock | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_cleanup.py::TestCancellationCustody::test_reconciliation_reclaims_cancelled_input_after_actor_sigkill` |
| 256 | old actor preserves input across immediate retry | Edge | Retry changes execution epoch while old actor holds input | Retried Job retains input and lease | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_cleanup.py::TestCancellationCustody::test_old_actor_preserves_input_across_immediate_retry` |
| 257 | expiry preserves terminal input held by actor | Edge | Expired lease still has live input owner | Expiry cannot remove bytes or lease | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_cleanup.py::TestCancellationCustody::test_expiry_preserves_terminal_input_held_by_actor` |
| 258 | discard preserves input held by actor | Edge | Discard races live input owner | Ownership-uncertain refusal preserves bytes and lease | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_cleanup.py::TestCancellationCustody::test_discard_preserves_input_held_by_actor` |
| 259 | waiting owner cancels promptly | Edge | New actor waits for exact inode ownership | Cancellation settles within two seconds; input survives | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_leases.py::TestOpenJobInput::test_waiting_owner_cancels_promptly` |
| 260 | waiting owner has a finite acquisition deadline | Error | Previous actor retains ownership | Finite acquisition timeout refuses rather than waiting forever | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_leases.py::TestOpenJobInput::test_waiting_owner_has_a_finite_acquisition_deadline` |
| 261 | rejects invalid acquisition timeout | Error | Zero, negative, infinite or NaN timeout | Invalid acquisition budget refuses before input access | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_leases.py::TestOpenJobInput::test_rejects_invalid_acquisition_timeout` |
| 262 | closes descriptor after session expiration | Edge | Session expires during capture spool use | Context closes its exact descriptor | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_leases.py::TestCaptureSpoolContext::test_closes_descriptor_after_session_expiration` |
| 263 | refuses replaced inode without truncating foreign bytes | Error | Capture spool inode replaced | Open refuses; replacement bytes remain intact | Integration | ✅ `backend/tests/integration/modules/ingestion/test_staging_leases.py::TestCaptureSpoolContext::test_refuses_replaced_inode_without_truncating_foreign_bytes` |
| 264 | backfill progresses during sustained interactive arrivals | Edge | Six pending arrivals maintained on real DBOS/native work | Foreground progresses; both backfills complete under continuing contention | E2E | ✅ `backend/tests/e2e/test_ingestion_fairness.py::TestIngestionFairness::test_backfill_progresses_during_sustained_interactive_arrivals` |
| 265 | full capacity backfill eventually completes | Edge | Full-capacity backfill competes with foreground work | Actual queued/admitted native ticket completes within 110 seconds | E2E | ✅ `backend/tests/e2e/test_ingestion_fairness.py::TestIngestionFairness::test_full_capacity_backfill_eventually_completes` |
| 266 | matches the in process engine | Edge | STL/3MF × 1/2/4 native slots; four concurrent demands | Geometry, image, strategy and fingerprints retain reference quality | Integration | ✅ `backend/tests/integration/modules/media/test_mesh_isolation.py::TestGenerate::test_matches_the_in_process_engine` |
| 267 | exported peak includes known descendant allocation | Happy | Known resident allocation in a real worker descendant | Exported peak includes descendant memory and exact ticket releases | Integration | ✅ `backend/tests/integration/modules/media/thumbnail_engine/test_resource_recovery.py::TestDescendantPeakExport::test_exported_peak_includes_known_descendant_allocation` |
| 268 | cancelled import retains source during concurrent dismiss | Edge | Copy/archive import is cancelled during active source read | Dismiss refuses while actor reads; later exact cleanup releases source | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_jobs.py::TestImportStepInputCustody::test_cancelled_import_retains_source_during_concurrent_dismiss` |
| 269 | cancelled import retains published slot during active read | Edge | Local or streamed published capture slot is cancelled while reader is active | Slot, lease and bytes survive active read; exact deletion waits for actor exit | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestPublishedSlotInputCustody::test_cancelled_import_retains_published_slot_during_active_read` |
| 270 | cancel before window claim never opens published source | Edge | Cancellation wins before prepared window claims authority | No source read occurs; slot, lease and disposable credit cleanly retire | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_capture_slots.py::TestPublishedSlotInputCustody::test_cancel_before_window_claim_never_opens_published_source` |
| 271 | records the resulting model when a direct import completes | Happy | Direct successful download seals exact output | Completed item retains its resulting Model and original payload | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_inbox.py::TestRunImportJob::test_records_the_resulting_model_when_a_direct_import_completes` |
| 272 | run import model files completes | Happy | Selected file successful download seals exact output | Completed item retains its resulting Model and original payload | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_inbox.py::TestRunImportJob::test_run_import_model_files_completes` |
| 273 | run import collection completes | Happy | Collection member successful download seals exact output | Completed item retains its resulting Model and original payload | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_inbox.py::TestRunImportJob::test_run_import_collection_completes` |


## Shared receipt contracts and publication ownership

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 274 | preserves the committed physical generation | Happy | Exact provider and physical fields persisted in a real ownership row | Public receipt keeps all exact fields and stays immutable after row mutation | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_receipts.py::TestOwnedReceipt::test_preserves_the_committed_physical_generation` |
| 275 | refuses incomplete persisted authority | Error | Persisted ownership lacks token or byte size | Shared public conversion refuses incomplete authority | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_receipts.py::TestOwnedReceipt::test_refuses_incomplete_persisted_authority` |
| 276 | provider binding is shared with existing ownership consumers | Edge | Existing ownership consumer and lower receipt contract resolve same destination | Same fingerprint and exception class; changing namespace changes binding | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_receipts.py::TestSharedReceiptIdentity::test_provider_binding_is_shared_with_existing_ownership_consumers` |
| 277 | failed replica publication keeps its durable reservation | Error | Provider publication fails after independently committed reservation | Caller rollback retains PENDING receipt, digest, provider, size and sanitized error; source survives | Integration | ✅ `backend/tests/integration/modules/backups/backup/test_opendal.py::TestOpenDalBackupReplication::test_failed_replica_publication_keeps_its_durable_reservation` |
| 278 | application respects its recorded boundaries | Error | Storage retirement and backup provider operations share receipt contracts | Application imports have no cycles, private cross-owner dependencies or unexported operation APIs | Repo | ✅ `backend/tests/repo/test_architecture.py::TestArchitecture::test_application_respects_its_recorded_boundaries` |
| 279 | retains source authority until scratch custody is released | Edge | User/system age or per-user capacity pruning sees terminal Job with retained source scratch | Pruning keeps Job authority until exact scratch custody releases, then removes eligible history | Integration | ✅ `backend/tests/integration/modules/work/test_jobs.py::TestPrune::test_retains_source_authority_until_scratch_custody_is_released` |

Focused receipt-contract and orchestration reproduction, from `backend/`:

```sh
uv run pytest -n 0 -q \
  tests/repo/test_architecture.py \
  tests/integration/modules/storage/test_storage_receipts.py \
  tests/unit/modules/backups/test_backup.py::TestBackupProviderIdentity \
  tests/integration/modules/backups/backup/test_opendal.py::TestOpenDalBackupReplication \
  tests/integration/modules/backups/backup/test_receipt_cleanup.py \
  tests/integration/modules/storage/test_storage_deletion.py \
  tests/integration/modules/storage/test_storage_ownership.py \
  tests/integration/modules/storage/storage_ownership/test_retirement.py
```

The new receipt mirror inherits the `integration-domains-b` shard. Source-custody
history retention is covered by the existing Work mirror and its shard selection.


| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 280 | refuses endpoints without a stable destination | Error | Malformed IPv6, missing scheme/host, invalid or out-of-range port | Public provider binding refuses an unpinnable destination | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_receipts.py::TestProviderReceiptBinding::test_refuses_endpoints_without_a_stable_destination` |
| 281 | backup settings match explicit binding and ignore rotated credentials | Edge | Configured legacy backup endpoint/region; credentials rotate or endpoint changes | Configuration and explicit identity agree; rotation preserves identity; endpoint change differs | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_receipts.py::TestProviderReceiptBinding::test_backup_settings_define_credential_independent_binding` |
| 282 | transport options bind destination but not credentials | Edge | S3/WebDAV options normalize endpoint; region/addressing/root changes | Credential rotation preserves binding; meaningful destination changes alter it | Integration | ✅ `backend/tests/integration/modules/storage/test_storage_receipts.py::TestProviderReceiptBinding::test_transport_options_bind_destination_but_not_credentials` |


## Existing consumer and failure-contract regressions

These rows preserve existing observable contracts while adapting consumers to
durable source custody and exact physical receipts. Runtime execution evidence
is recorded in the PR; a row names its assertion rather than implying a CI result.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 283 | upgrade retains owned archive locators | Edge | Lowercase historical committed archive receipt before full chain | All historical columns survive upgrade/roundtrip; fresh legacy generation and recovery field asserted separately | Integration | ✅ `backend/tests/integration/db/migrations/test_backup_runs_migration.py::TestBackupRunUpgrade::test_upgrade_retains_owned_archive_locators` |
| 284 | upgrade retains historical volume without certification | Edge | Coherent historical committed receipt and absent/zero/negative/tiny/positive volume | Historical volume remains unassessed; complete current schema converges | Integration | ✅ `backend/tests/integration/db/migrations/test_mesh_volume_evidence.py::TestVolumeUpgrade::test_upgrade_retains_historical_volume_without_certification` |
| 285 | roundtrip preserves historical measurements | Edge | Coherent historical committed receipt and tiny volume | Historical scalar and slicer facts survive downgrade/reupgrade and full-chain parity | Integration | ✅ `backend/tests/integration/db/migrations/test_mesh_volume_evidence.py::TestVolumeUpgrade::test_roundtrip_preserves_historical_measurements` |
| 286 | upgrade retires invalid dimension facts | Error | Negative or nonfinite dimensions on each axis | Invalid dimension removed; identity and other facts survive full chain | Integration | ✅ `backend/tests/integration/db/migrations/test_mesh_volume_evidence.py::TestHistoricalDimensionRepair::test_upgrade_retires_invalid_dimension_facts` |
| 287 | adopts a matching legacy object | Happy | Existing local bytes match requested digest and size | Exact device/inode/ctime/size receipt matches; original payload survives | Unit | ✅ `backend/tests/unit/modules/storage/storage_backend/test_local_ownership.py::TestLocalAdoption::test_adopts_a_matching_legacy_object` |
| 288 | adopts equal bytes as a distinct physical generation | Edge | Prior inode remains alive while equal-byte replacement occupies canonical locator | Replacement has distinct inode/token; stale receipt refuses; both payloads survive | Unit | ✅ `backend/tests/unit/modules/storage/storage_backend/test_local_ownership.py::TestLocalAdoption::test_adopts_equal_bytes_as_a_distinct_physical_generation` |
| 289 | guarded storage requires structured one shot confirmation | Error | Guarded local adapter retains actual inode evidence | Structured 409 risk prompt; original bytes remain | Integration | ✅ `backend/tests/integration/api/v1/models/test_trash.py::TestPurgeModel::test_guarded_storage_requires_structured_one_shot_confirmation` |
| 290 | guarded storage accepts one shot confirmation | Happy | Explicit risk confirmation on guarded adapter with exact evidence | Logical purge completes; blocked physical bytes remain | Integration | ✅ `backend/tests/integration/api/v1/models/test_trash.py::TestPurgeModel::test_guarded_storage_accepts_one_shot_confirmation` |
| 291 | hard delete aborts when owned storage is suddenly unmounted | Error | Enrolled root replaced while trashed Artifact remains indexed | GC reports blocked resource; row and detached original bytes survive; no deletion intent queued | Integration | ✅ `backend/tests/integration/modules/library/trash/test_gc.py::TestHardDelete::test_hard_delete_aborts_when_owned_storage_is_suddenly_unmounted` |
| 292 | leaves the row in place when storage is read only | Error | Destructive access probe raises PermissionError | GC reports blocked resource; row and payload survive; no deletion intent queued | Integration | ✅ `backend/tests/integration/modules/library/trash/test_gc.py::TestHardDelete::test_leaves_the_row_in_place_when_storage_is_read_only` |
| 293 | hard delete on remote backend respects blob ownership | Error | Successful remote access probe but unreceipted vault file plus external NAS file | Only vault key is probed; no vault/external/thumbnail deletion; all rows survive ownership refusal | Integration | ✅ `backend/tests/integration/modules/library/trash/test_trash_remote_backend.py::TestHardDeleteModel::test_hard_delete_on_remote_backend_respects_blob_ownership` |
| 294 | artifact upload is committed through webdav | Happy | Production setup/composition and actual API upload into loopback WebDAV | Original stored bytes and COMMITTED receipt; purge returns full structured guarded-risk prompt while Artifact and bytes survive | E2E | ✅ `backend/tests/e2e/test_e2e_webdav_storage.py::TestArtifactUpload::test_artifact_upload_is_committed_through_webdav` |
| 295 | rejects oversized downloads without leaking temporary custody | Error | Real HTTP response larger than the upload limit | download_too_large; no new scratch receipts, capacity credits or payload directories | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestDownloadToStaging::test_download_to_staging_enforces_size_limit` |
| 296 | downloads real file bytes into receipt-owned temporary staging | Happy | Real HTTP STL download; hardlinks and unsupported-hardlink modes | Original filename and bytes; exact inode receipt and durable credit; discard removes payload, window and credit | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestDownloadToStaging::test_download_to_staging_fetches_real_file` |
| 297 | imports each collection member as its own Model | Happy | A remote collection with multiple members | One Model per member with distinct content and original source URL | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_collection_auto_import_creates_models_per_member` |
| 298 | imports only the selected collection member | Happy | Collection manifest followed by one member selection | Only the chosen member is imported and its source URL is retained | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_collection_review_then_select_imports_chosen_member` |
| 299 | names the Model from the downloaded filename | Happy | Model page URL resolving to a named download | The resulting Model has the download-derived name | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_import_from_url_names_model_from_download` |
| 300 | imports real Benchy STL and records its Printables source | Happy | Real Benchy fixture downloaded from a resolved Printables URL | STL Artifact with original size and source URL; committed testdata remains intact | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_import_real_benchy_from_printables_url_records_source` |
| 301 | imports selected files from a real MakerWorld ZIP | Happy | Real ZIP response followed by selected archive entries | Archive manifest and selected Artifacts retain the original source URL | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_import_real_benchy_zip_from_makerworld_url` |
| 302 | rejects an HTML response from an unrecognized host | Error | An unrecognized host returns HTML rather than a usable file | Job fails with url_not_a_direct_file and the unusable payload is discarded | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_import_url_unrecognised_host_html_fails_gracefully` |
| 303 | creates a page-files manifest and imports selected files | Happy | Model page file listing followed by a file selection | Manifest preserves the listed files and only requested selections are imported | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestImportFromUrl::test_model_page_files_manifest_then_select` |
| 304 | deduplicates a recaptured Artifact while retaining provenance overrides | Happy | Offline capture and recapture of identical G-code bytes | First result imported and second deduplicated; one File, Model and provenance link; user override retained | E2E | ✅ `backend/tests/e2e/test_browser_capture.py::TestBrowserCapture::test_offline_capture_import_recapture_deduplicates_durable_artifact` |
| 305 | retries only the failed selection after a partial capture import | Error | Two selected files; the bad child publication fails once | Completed partial result contains imported and failed children; retry preserves the durable sibling and imports only the failed child | E2E | ✅ `backend/tests/e2e/test_browser_capture.py::TestBrowserCapture::test_offline_capture_partial_result_retries_only_failed_selection` |
| 306 | retries each named Inbox item through its resolve Job | Happy | Batch retry of Inbox items requiring resolution | Draining Jobs records exactly the original requested item ids with valid Job ownership | Integration | ✅ `backend/tests/integration/api/v1/inbox/test_batch.py::TestBatchItems::test_retries_every_named_item` |
| 307 | resolves a captured source through background work | Happy | Public URL capture followed by engine drain | Exactly the newly created Inbox item is resolved | Integration | ✅ `backend/tests/integration/api/v1/inbox/test_capture.py::TestCapture::test_resolves_the_source_in_the_background` |
| 308 | schedules another resolution for a failed Inbox item | Happy | A failed Inbox item requiring fresh resolution | The new Job resolves the correct item with the expected subject and execution epoch | Integration | ✅ `backend/tests/integration/api/v1/inbox/test_lifecycle.py::TestResolveItem::test_schedules_a_failed_item_to_be_resolved_again` |
| 309 | resolves an Inbox item again when its manifest is missing | Edge | Retry of an Inbox item with no manifest | Fresh resolution runs for the correct item | Integration | ✅ `backend/tests/integration/api/v1/inbox/test_lifecycle.py::TestRetryItem::test_schedules_a_fresh_resolve_for_an_item_with_no_manifest` |
| 310 | imports a single URL file through its owned window | Happy | Direct STL URL with fixture bytes | Job completes and persists the original STL Artifact through the actual window and Job owner | Integration | ✅ `backend/tests/integration/api/v1/ingest/test_import_and_formats.py::TestImportFromUrl::test_import_from_url_single_file` |
| 311 | runs URL ingestion through to a completed Job | Happy | Direct URL returning cube.stl | Job completes with the original file content staged and sealed inside its owned window | Integration | ✅ `backend/tests/integration/api/v1/ingest/test_ingest_routes.py::TestIngestUrl::test_runs_a_url_ingest_through_to_a_completed_job` |
| 312 | releases temporary staging after download cancellation | Edge | HTTP stream cancelled after its first chunk | OperationCancelled; stream closed; no new scratch receipts, credits or payload directories | Integration | ✅ `backend/tests/integration/modules/ingestion/importer/test_cancellation.py::TestDownloadWithdrawal::test_releases_staging_after_chunk_cancellation` |
| 313 | expands an archive into its original named entries | Happy | Downloaded ZIP containing two importable files | Two entries are consumed with their original member names | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadAssets::test_expands_an_archive` |
| 314 | stages a plain download as one file | Happy | Downloaded plain STL fixture | One consumed entry retains its original name and bytes | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadAssets::test_stages_a_plain_file` |
| 315 | creates one asset per importable archive entry | Happy | Downloaded ZIP with multiple importable entries | Exactly one consumed asset exists for each importable member | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_expands_a_zip_into_one_asset_per_entry` |
| 316 | expands ZIP content served without a ZIP filename | Edge | ZIP bytes downloaded as bundle.bin | Content is expanded and the original member path is retained | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_expands_a_zip_that_is_not_named_zip` |
| 317 | gives each expanded archive entry a distinct result key | Happy | One selected ZIP containing two entries | Each consumed entry has its own result key | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_gives_each_expanded_entry_its_own_result_key` |
| 318 | hashes the staged download content | Happy | Plain STL fixture staged and consumed through its window | Consumed asset retains its content hash with the expected digest length | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_hashes_what_it_staged` |
| 319 | retains selection identity on every expanded archive entry | Happy | One selected ZIP containing multiple entries | Every consumed entry retains the original source selection id | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_keeps_every_expanded_entry_pointing_at_its_selection` |
| 320 | keeps a 3MF as one complete asset | Edge | A 3MF package that is also a ZIP container | One self asset is consumed and the 3MF is not expanded | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_leaves_a_3mf_whole` |
| 321 | records each entry path within its source container | Happy | Downloaded ZIP containing nested/a.stl | The consumed asset retains the exact container entry path | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_records_where_in_the_container_each_entry_came_from` |
| 322 | stages a plain file as one selected asset | Happy | Plain STL download inside the supplied window | One self asset contains the original file bytes | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset::test_stages_a_plain_file_as_one_asset` |
| 323 | stops slot copying between entries without losing the committed Artifact | Edge | Two real capture slots created, uploaded and finalized; withdrawal after the first consume | OperationCancelled; first Artifact durable; second copy never starts; both sources and leases retained; no scratch receipt or credit remains | Integration | ✅ `backend/tests/integration/modules/ingestion/inbox/test_staging.py::TestStageCaptureUploadSlotAssets::test_stops_slot_copying_between_entries` |
| 324 | follows redirects and releases the owned download | Edge | Real HTTP redirect to final.stl | Final URL filename and bytes; exact scratch receipt and capacity credit are released | Contract | ✅ `backend/tests/contract/api/v1/test_ingest.py::TestDownloadToStaging::test_download_to_staging_follows_redirect` |
| 325 | captures a supported browser source with a named API key | Happy | Named API key and supported browser source URL | Capture returns 202 with browser source kind; listing retains URL, title and correct owner | E2E | ✅ `backend/tests/e2e/test_browser_capture.py::TestBrowserCapture::test_named_api_key_captures_supported_browser_source_for_pending_imports` |
| 326 | reports storage failure | Error | Prepared STL publication raises an I/O failure | STL request reports the existing storage failure contract | Integration | ✅ `backend/tests/integration/api/v1/files/test_stl.py::TestFileAsStl::test_reports_storage_failure` |
| 327 | preserves deadline failure while releasing its temporary bundle | Error | Owned build process writes partial bundle then does not settle | CLI records deadline failure and removes partial bundle with its owned directory | Integration | ✅ `frontend/scripts/viewer-representation-pilot.test.mjs::preserves deadline failure while releasing its temporary bundle` |


## Overlapping discovery passes

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 328 | discovers new work after an overlapping pass loses its claim | Edge | Actual queued Inline pass overlaps its holder | Next nudge queues immediately and discovers the new subject | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestPass::test_overlapping_loser_does_not_cover_the_next_nudge` |
| 329 | acknowledges only elapsed queue stamps | Edge | Older/newer stamps and replacement holder | Paired marker cleared only when elapsed; holder, expiry and dirty stamp preserved | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestPass::test_losing_claim_acknowledges_only_its_elapsed_queue_stamp` |
| 330 | queues continuation after bounded handoff | Edge | Competing passes during 20 iterations | One fresh continuation queued; subsequent upload discovered | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestPass::test_bounded_handoff_queues_after_competing_passes_have_finished` |


## Core measurement and rendering qualification

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 331 | scalar factory keeps measurement unassessed | Happy | None, zero, negative legacy scalar, positive finite scalar (4) | Scalar retained; state remains legacy_unassessed and method absent | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeLegacyUnassessed::test_scalar_factory_keeps_measurement_unassessed` |
| 332 | scalar factory rejects nonfinite legacy input | Error | Boolean, numeric string, NaN, integer beyond float64 range (4) | ValueError for an invalid finite scalar | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeLegacyUnassessed::test_scalar_factory_rejects_nonfinite_legacy_input` |
| 333 | rejects inconsistent unavailable evidence | Error | Unavailable wire with scalar, missing method or untyped cause (3) | ValueError rejects contradictory unavailable evidence | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_rejects_inconsistent_unavailable_evidence` |
| 334 | rejects inconsistent not calculated evidence | Error | Not-calculated wire with scalar, invented method or untyped cause (3) | ValueError rejects evidence that was not calculated | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_rejects_inconsistent_not_calculated_evidence` |
| 335 | rejects legacy scalar with measurement evidence | Error | Legacy scalar wire claims method or topology cause (2) | ValueError prevents promotion into assessed evidence | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestVolumeWire::test_rejects_legacy_scalar_with_measurement_evidence` |
| 336 | accepts partial finite dimensions | Happy | Absent/unassessed axes, zero/tiny-positive/finite dimensions, unrelated metadata (4) | Accepted without mutating metadata | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestGeometryExtents::test_accepts_partial_finite_dimensions` |
| 337 | rejects unrepresentable dimension | Error | Each of three axes with negative, +/-infinity, NaN, boolean, string, mapping or overflowing integer (24) | InvalidMeshMeasurements with the finite/nonnegative contract error | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_measurements.py::TestGeometryExtents::test_rejects_unrepresentable_dimension` |
| 338 | reports a missing numpy or pillow as an error | Error | Actual import boundary refuses NumPy or Pillow; logger present/absent (4, expanded existing test) | No thumbnail; provided logger records dependency error and no warning | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_reports_a_missing_numpy_or_pillow_as_an_error` |
| 339 | reports malformed mesh preparation | Error | Two-coordinate vertices with a three-corner face (1) | No thumbnail; warning identifies the source file; no dependency error | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderMeshThumbnail::test_reports_malformed_mesh_preparation` |
| 340 | leaves frame unchanged for subpixel triangle | Edge | Positive-area on-screen triangle containing no pixel centre (1) | Positive candidate work but RGB frame remains unchanged | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_rasterizer.py::TestRasteriseTriangles::test_leaves_frame_unchanged_for_subpixel_triangle` |
| 341 | refuses missing pixel dependency | Error | Valid prepared geometry; NumPy/Pillow import refusal; logger present/absent (4) | No frame; provided logger records dependency error and no warning | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_refuses_missing_pixel_dependency` |
| 342 | large preview preserves native pixel dimensions | Happy | 641x48 preview; resize boundary rejects any unintended downsampling (1) | Exact requested dimensions; nonempty foreground; transparent background | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedPixels::test_large_preview_preserves_native_pixel_dimensions` |
| 343 | refuses failed image encoding | Error | Real image encoder save boundary raises OSError; logger present/absent (2) | No partial thumbnail; provided logger reports source-specific warning, no dependency error | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_rasterizer.py::TestRenderPreparedThumbnail::test_refuses_failed_image_encoding` |

## Nearest-neighbor numerical failure qualification

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 344 | reports native index build failure | Error | Valid finite target; native KDTree raises ValueError or FloatingPointError | Public GeometryError numeric_range preserves original native exception as its cause | Unit | ✅ `backend/packages/printstash-core/tests/mesh/similarity/test_point_neighbors.py::TestPointNeighbors::test_reports_native_index_build_failure` |
| 345 | rejects native distance overflow | Error | Zero target; finite source coordinate 1e200 whose native squared distance overflows | Public GeometryError numeric_range refuses an invalid nearest-distance result | Unit | ✅ `backend/packages/printstash-core/tests/mesh/similarity/test_point_neighbors.py::TestPointNeighbors::test_rejects_native_distance_overflow` |
| 346 | rejects unrepresentable physical norm | Error | Finite target components 1.3e308 on two axes; zero query; real native index | Public GeometryError numeric_range refuses a physical Euclidean norm beyond float64 | Unit | ✅ `backend/packages/printstash-core/tests/mesh/similarity/test_point_neighbors.py::TestPointNeighbors::test_rejects_unrepresentable_physical_norm` |

Fresh core qualification: the three affected files pass 308 cases; measurements
and rasterizer each reach 100% combined statement/branch coverage and nearest
neighbors reach 97.96%. The package audit passes 2,382 cases, then all five floor
checks at 99.22% aggregate coverage. No floors or exclusions changed. Ruff 0.16.10
accepts the Python 3.14 target and passes backend/core source and tests.

## Selective load qualification

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 347 | selects requested LOAD cells | Happy | Default all; eight only; reordered four/one | Parsed selection retains intended cells and order | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestParseArgs::test_selects_requested_load_cells` |
| 348 | rejects invalid LOAD selection | Error | Duplicates; unsupported three; empty list; outside LOAD | CLI exits2 without creating output | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestParseArgs::test_rejects_invalid_load_selection` |
| 349 | changes only requested user lease count | Edge | All cells; eight only; four/one | Environment override contains only logical per-user staging limit8/8/4 | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestLoadAdmissionEnvironment::test_changes_only_the_requested_user_lease_count` |
| 350 | reports scaling as unassessed | Edge | Serial or four baseline absent, including eight-only | Ratio null; assessed false; gate null; explicit missing-baseline reason | Unit | ✅ `backend/tests/unit/scripts/test_qualify_ingestion.py::TestMissingLoadBaselines::test_reports_scaling_as_unassessed` |
| 351 | records every default LOAD cell | Happy | Fresh real app, all four cells | Default selection complete; report actual per-user8, global32,4GiB bytes,1GiB free floor; native1 unchanged | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_records_every_load_cell` |
| 352 | records selected LOAD cells | Edge | Eight-only/eight real samples; reordered four/one baselines | Only selected cells run; all accepted artifacts usable; profile matches largest cell; scaling assessment truthful; natural drain complete | Integration | ✅ `backend/tests/integration/scripts/test_qualify_ingestion.py::TestMain::test_records_selected_load_cells` |

## Paired-browser publication transaction

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 353 | publishes paired-browser upload on disk SQLite | Happy | Real paired-device create/PUT with durable SQLite | Uploaded slot, exact bytes, committed receipt authority, no spool and review finalization | Integration | ✅ `backend/tests/integration/api/v1/inbox/test_capture_upload.py::TestPutCaptureUploadSlot::test_publishes_paired_browser_upload_on_disk_sqlite` |

Paired-device authentication updates its last-used audit time. Capture publication
commits that authentication metadata before preparing storage on an independent
connection, then validates slot ownership in the publication transaction. The
clean-transaction guard and revocation checks remain enforced. The disk-backed
HTTP regression reproduced the original guard failure before this fix.

## Browser cancellation and worker cleanup

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 354 | recognizes explicit cancellation before/after browser failure | Edge | same GET/URL; AbortError proof arrives on either side of ERR_ABORTED | no problem remains; one cancellation consumes one failed request | Frontend unit | ✅ `frontend/tests/repo/page-problems.test.ts > page request problems > recognizes explicit cancellation before the browser failure; recognizes explicit cancellation after the browser failure` |
| 355 | reports an uncorrelated abort on the same endpoint | Error | two identical aborted GETs, one explicit cancellation | second ERR_ABORTED remains visible | Frontend unit | ✅ `frontend/tests/repo/page-problems.test.ts > page request problems > reports an uncorrelated abort on the same endpoint` |
| 356 | preserves connection reset / ERR_FAILED despite cancellation | Error | explicit cancellation plus a distinct error | original network error remains visible | Frontend unit | ✅ `frontend/tests/repo/page-problems.test.ts > page request problems > preserves net::ERR_CONNECTION_RESET despite cancellation; preserves net::ERR_FAILED despite cancellation` |
| 357 | does not confuse URL / HTTP method | Edge | GET proof with POST or changed query failure | both failures remain visible | Frontend unit | ✅ `frontend/tests/repo/page-problems.test.ts > page request problems > does not confuse another URL or HTTP method with the canceled request` |
| 358 | accepts explicit AbortSignal cancellation of pending fetch | Edge | real Chromium fetch remains pending, then controller.abort | fetch AbortError; actual requestfailed; collector eventually empty | Playwright | ✅ `frontend/tests/e2e/page-problems.spec.ts > page problem collection > accepts an explicit AbortSignal cancellation of a pending fetch` |
| 359 | reports browser abort without app cancellation | Error | route.abort(aborted), no canceled signal | actual ERR_ABORTED retained | Playwright | ✅ `frontend/tests/e2e/page-problems.spec.ts > page problem collection > reports a browser abort without an app cancellation` |
| 360 | preserves server errors in collector | Error | real fetch receives 500 | bad response 500 retained | Playwright | ✅ `frontend/tests/e2e/page-problems.spec.ts > page problem collection > preserves server errors in the collector` |
| 361 | reports terminal preview failure without retrying | Error | timeout, invalid_source, disabled, cancelled, unknown provider failure | expected failed reason; one fetch; no object URL | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts > useStlPreview > reports terminal timeout without retrying or creating a preview; reports terminal invalid_source without retrying or creating a preview; reports terminal derivative_group_disabled without retrying or creating a preview; reports terminal cancelled without retrying or creating a preview; reports terminal unexpected_provider_failure without retrying or creating a preview` |
| 362 | does not request absent preview source | Edge | source null | pending state; zero fetches | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts > useStlPreview > does not request an absent preview source` |
| 363 | discards late bytes from obsolete source | Edge | switch File 1→2, old response resolves after new | old signal aborted; exactly one URL generated; new preview retained | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts > useStlPreview > discards late bytes from an obsolete source` |
| 364 | does not surface canceled rejection after unmount | Edge | pending request rejected AbortError after unmount | one fetch; no preview URL / unhandled rejection | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts > useStlPreview > does not surface a canceled load rejection after unmount` |
| 365 | explains preview failures to user | Error | all six public failure variants | exact useful message for each variant | Frontend unit | ✅ `frontend/src/lib/__tests__/use-stl-preview.test.ts > stlPreviewMessage > explains resource_limit to the user; explains timeout to the user; explains invalid_source to the user; explains derivative_group_disabled to the user; explains cancelled to the user; explains worker_failed to the user` |
| 366 | returns invalid-input error and releases worker | Error | worker returns typed invalid reply | invalid code; terminate once | Frontend unit | ✅ `frontend/src/lib/__tests__/gcode-worker-client.test.ts > Cancelable toolpath parsing > reports invalid input after worker cleanup` |
| 367 | releases crashed worker and removes cancellation listener | Error | ErrorEvent followed by abort | worker_failed rejection; terminate once | Frontend unit | ✅ `frontend/src/lib/__tests__/gcode-worker-client.test.ts > Cancelable toolpath parsing > retires a crashed worker` |
| 368 | preserves dispatch failure and releases worker | Error | postMessage throws DataCloneError followed by abort | same error object; terminate once | Frontend unit | ✅ `frontend/src/lib/__tests__/gcode-worker-client.test.ts > Cancelable toolpath parsing > retires the worker after dispatch failure` |

The paired-browser fix passes all 44 capture-route cases and the actual extension
capture boundary against a fresh backend. Qualification argument/admission checks
pass 63 unit cases. The browser collector and viewer/worker failure contracts pass
32 focused frontend cases. Configured backend Pyright reports zero errors or
warnings. These results do not replace the pending full soak or exact-head Deep CI.

The private eight-only LOAD control completes all eight useful Artifacts with
one native worker, then drains naturally; its serial-worker sample deadline is
120 seconds. The initial 60-second fixture bound accepted all eight uploads but
expired three previews behind queued work; that failed run is retained. The
reordered four/one control also completed with truthful keyed baseline reporting.

The affected browser selection passed 19 cases and timed out once during initial
navigation before collector setup. The same responsive-navigation case passed
once in isolation (5.5 seconds) without a code change; this records the transient
failure rather than claiming a diagnosed fix. All three actual cancellation/HTTP
failure contracts passed. Frontend suite hygiene passes its four checks.

## Terminal engine recovery

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 369 | Frees a terminal attempt on a lost executor | Happy | Completed, failed or cancelled Job; exact current attempt running on a stale executor | Engine attempt cancelled; maintenance running count becomes zero; durable Job outcome, result, finish time, epoch and attempt remain unchanged; repeat sweep is a no-op | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_frees_a_terminal_attempt_on_a_lost_executor` |
| 370 | Preserves executions without terminal lost-owner authority | Edge | Live, unregistered or absent executor; nonterminal or missing Job; superseded epoch or attempt; queued execution; reconcile pass | No cancellation; original engine execution status remains unchanged | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_preserves_executions_without_terminal_lost_owner_authority` |
| 371 | Preserves an execution reassigned during inspection | Edge | Initial lost-executor snapshot; engine evidence changes to a live executor | Fresh engine ownership excludes the execution from cancellation | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_rechecks_engine_owner_before_cancelling` |
| 372 | Preserves an executor that renews during inspection | Edge | Initial stale heartbeat; same executor renews before cancellation | Fresh executor liveness excludes the execution; engine status remains running | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_preserves_an_executor_that_renews_during_inspection` |
| 373 | Retries a failed engine cancellation | Error | Exact terminal lost attempt; first engine cancellation raises OSError | Failure is logged; attempt remains running; later sweep cancels it successfully | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_retries_a_failed_engine_cancellation` |
| 374 | Recovers a terminal lost attempt through the periodic tick | Happy | Terminal attempt running on a stale executor | Periodic tick cancels the attempt and frees maintenance capacity | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_periodic_tick_recovers_a_terminal_lost_attempt` |
| 375 | Reaches an eligible terminal attempt after excluded candidates | Edge | Three nonterminal candidates precede an eligible terminal attempt; configured SQL batch size is two | Eligible tail attempt is cancelled; all three nonterminal executions remain running | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_reaches_valid_terminal_attempts_after_unowned_candidates` |
| 376 | Closes owned SQL sessions before engine cancellation | Edge | Exact terminal lost attempt; session factory tracks active owned sessions | Engine cancellation observes zero active owned SQL sessions and cancels the exact attempt | Integration | ✅ `backend/tests/integration/modules/work/test_reconciler.py::TestSweepLostTerminalAttempts::test_closes_owned_sql_sessions_before_engine_cancellation` |
| 377 | Recovers a predecessor terminal execution at startup | Happy | Vault owner restarts after a previous API executor left a completed scratch-cleanup Job running in the engine | Startup retires the predecessor execution and frees maintenance capacity; completed Job outcome and stored result remain unchanged | Integration | ✅ `backend/tests/integration/bootstrap/test_work.py::TestStart::test_frees_its_predecessors_terminal_job_execution` |

The affected reconciler/bootstrap selection passed **128 cases in 13.61s**. The unchanged real SIGKILL scratch recovery E2E separately passed **1 case in 28.76s**. These overlapping selections are not summed. Recovery cancels only the exact terminal attempt whose current engine executor is lost, rechecking both Job authority and executor liveness without holding owned SQL sessions across engine cancellation.

## Inference readiness above FD_SETSIZE

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 378 | observes ready bytes above FD_SETSIZE | Happy | actual POSIX pipe read descriptor duplicated by F_DUPFD ≥1024, one byte written | readable is true; the unchanged byte is read afterward | Integration | ✅ `backend/tests/integration/modules/inference/local/test_residency.py::TestDescriptorReadiness::test_observes_ready_bytes_above_fd_setsize` |
| 379 | bounds an unready high descriptor | Edge | actual POSIX pipe read descriptor duplicated by F_DUPFD ≥1024, writer remains open without data | zero-duration readiness check returns false without fd range error | Integration | ✅ `backend/tests/integration/modules/inference/local/test_residency.py::TestDescriptorReadiness::test_bounds_an_unready_high_descriptor` |
| 380 | parent death releases warm residency | Edge | actual warm ONNX worker; retain source pipe writer after parent death | parent report received within 10s; warm child's pidfd readable within 5s; full residency reservation reacquired | Integration | ✅ `backend/tests/integration/modules/inference/local/test_residency.py::TestInferenceResidency::test_parent_death_releases_warm_residency[True]` |
| 381 | first frame precedes retirement | Edge | actual native retirement probe with queued pressure before first request | ready stderr within 5s; stdout remains unready 0.3s; first frame produces expected vectors; worker exits 73 | Integration | ✅ `backend/tests/integration/modules/inference/local/test_residency.py::TestResidencyRetirementRace::test_new_worker_handles_its_first_frame_before_retirement` |

The high-FD class and two original CI failures passed **4 cases in 7.22s** after a valid RED reproduced FD1024 rejection by select. The test readiness helper uses a context-managed DefaultSelector; production exchange already used that owner. The fixture raises only its current process soft RLIMIT_NOFILE when necessary and restores it after closing its own descriptors. The two high-FD cases are generated only on POSIX; a hard limit below the required descriptor fails explicitly. Linux CI executes both actual high-descriptor cases. Original readiness and quiet-period deadlines remain unchanged.

## Scoped CI PID namespace preparation

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 382 | provisions real PID namespaces for each full suite | Happy | Deep backend coverage / Python compatibility full jobs | Every applicable job invokes the shared preparation script unconditionally before its suite, refuses ignored preparation failures, schedules scoped cleanup after its suite with always() | Repo | ✅ `backend/tests/repo/test_ci_workflows.py::TestDeepSuite::test_provisions_real_pid_namespaces_for_each_full_suite` |
| 383 | namespace preparation preserves the host restriction | Edge | Runner requires an AppArmor user namespace exception | Policy attaches only to the copied runner-local unshare path with userns permission; dedicated profile is removed; no global sysctl or AppArmor disable appears | Repo | ✅ `backend/tests/repo/test_ci_workflows.py::TestDeepSuite::test_namespace_preparation_preserves_the_host_restriction` |
| 384 | namespace preparation requires a successful probe | Error | Host rejects the real unshare user/PID namespace probe | Script uses fail-fast execution, runs its probe outside the AppArmor-presence conditional, exports its PATH only after the probe succeeds, provides no ignored-failure fallback | Repo | ✅ `backend/tests/repo/test_ci_workflows.py::TestDeepSuite::test_namespace_preparation_requires_a_successful_probe` |
| 385 | shared ledger retains credit across a PID namespace | Happy | Real child process enters distinct user/PID namespace with a held native credit | Namespace identity differs from parent; live child retains the shared credit; process death permits exact reclaim | Integration | ✅ `backend/tests/integration/runtime/test_native_admission.py::TestNamespace::test_shared_ledger_retains_credit_across_pid_namespace` |

Deep full-suite jobs prepare an isolated runner-local unshare executable and, when required by the runner, a dedicated per-executable AppArmor userns profile. A successful real user/PID namespace probe is required before exporting the executable directory; an always-run cleanup unloads only that profile. Host-wide restrictions and the real shared-ledger assertion remain unchanged. The focused namespace repository selection passed **5 cases in 13.61s** (19.61s wall time); the actual remote namespace/profile probe remains pending exact-head CI. The previous failing namespace report is retained.

## Compatibility suite cleanup contract

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 386 | keeps the current Python full suite before scoped cleanup | Edge | Deep compatibility job has a namespace cleanup step after its test suite | Exactly one named compatibility suite runs full -q; exactly one later cleanup runs with always() and the scoped script | Repo | ✅ `backend/tests/repo/test_python_runtime.py::TestPythonRuntime::test_runs_ci_with_current_python` |

Final validation follows the requested order: ordinary PR CI, merge to main, then Deep CI on GitHub for the merged revision. The sustained resource qualification remains a separate P28 closure requirement.

The final high-FD, original inference regression, skip-hygiene and current-Python compatibility selection passes 6 cases in 14.53s. This checks the unchanged real descriptor behavior plus the repository contracts exposed by PR CI.

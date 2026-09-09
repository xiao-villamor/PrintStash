# Verified Vault migration — behaviour coverage

Scope: [issue #104](https://github.com/xiao-villamor/PrintStash/issues/104) and its
[attached implementation plan](https://github.com/user-attachments/files/31666725/printstash-vault-storage-migration-plan.md).
UI cases are tracked separately in [the UI matrix](vault-migration-ui-test-matrix.md).

✅ means the named assertion has passed locally; ❌ is outstanding; ⏭️ means
explicitly omitted. No row is inferred from coverage percentages.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `test_local_backend_retains_its_roots_when_configuration_changes` | Invariant | Runtime settings change after adapter construction | Old adapter derives old keys | Integration | ✅ |
| 2 | `test_candidate_local_backend_never_changes_active_settings` | Invariant | Construct candidate | Active settings unchanged | Integration | ✅ |
| 3 | `test_reader_keeps_source_adapter_after_activation` | Invariant | Old read crosses activation | Read retains old adapter; new reads use destination | Integration | ✅ |
| 4 | `test_activation_cannot_mix_an_unplanned_reader` | Boundary | Unplanned read still admitted | Activation times out before publication | Integration | ✅ |
| 5 | `test_retention_exemption_is_exactly_one_candidate` | Invariant | Source, destination and unrelated adapter | Only exact candidate destruction admitted | Integration | ✅ |
| 6 | `test_baseline_copies_trash_without_deleting_source` | Happy path | Trashed owned Artifact | Copied, verified, source unchanged | Integration | ✅ |
| 7 | `test_destination_collision_keeps_foreign_bytes` | Failure | Nonempty destination | Preflight refuses; foreign bytes retained | Integration | ✅ |
| 8 | `test_stale_plan_cannot_start` | Boundary | Wrong preflight digest | Start refuses without phase change | Integration | ✅ |
| 9 | `test_source_audit_refuses_missing_primary` | Failure | Missing primary Artifact | Quick audit blocks preflight | Integration | ✅ |
| 10 | `test_cutover_includes_baseline_ingestion_delta` | Happy path | New ingestion after baseline | Both Artifacts activated; source retained; post-audit clean | Integration | ✅ |
| 11 | `test_baseline_blocks_logical_purge_before_claiming_rows` | Invariant | Soft trash during baseline | Soft trash succeeds; purge changes no claim or bytes | Integration | ✅ |
| 12 | `test_baseline_blocks_provider_reconfiguration` | Invariant | Provider update during baseline | Configuration rejected; source remains bound | Integration | ✅ |
| 13 | `test_baseline_blocks_restore_before_backup_lookup` | Invariant | Restore during baseline | Refused before backup lookup | Integration | ✅ |
| 14 | `test_restart_before_activation_requires_explicit_resume[draining]` | Recovery | Crash after durable draining phase | Source recovered paused; explicit resume skips verified copy | Integration | ✅ |
| 15 | `test_restart_before_activation_requires_explicit_resume[delta_copy]` | Recovery | Crash after durable delta phase | Source recovered paused; subsequent activation succeeds | Integration | ✅ |
| 16 | `test_restart_before_activation_requires_explicit_resume[verifying]` | Recovery | Crash after verification phase | Source remains authoritative until resumed activation | Integration | ✅ |
| 17 | `test_restart_before_activation_requires_explicit_resume[activating]` | Recovery | Crash before activation transaction | Source recovered; no partial destination authority | Integration | ✅ |
| 18 | `test_restart_after_database_commit_keeps_destination_authoritative` | Recovery | Crash after database commit, before rebind | Destination recovered; catalogue maps to copied bytes | Integration | ✅ |
| 19 | `test_changed_destination_receipt_is_never_adopted_on_resume` | Failure | Replaced destination inode | Nonretryable pause; replacement and source unchanged | Integration | ✅ |
| 20 | `test_local_destination_cannot_overlap_source_roots` | Boundary | Destination nested beneath source | Preflight refuses overlapping roots | Integration | ✅ |
| 21 | `test_http_stream_retains_coherent_source_generation` | Concurrency | Stream response spans activation | Exact old-generation body returned | Integration | ✅ |
| 22 | `test_cutover_drains_background_work_after_response_headers` | Concurrency | Work continues after response headers | Cutover waits until background operation exits | Integration | ✅ |
| 23 | `test_empty_journal_allows_normal_startup` | Boundary | No journal | Normal startup admitted | Unit | ✅ |
| 24 | `test_completed_other_run_cannot_mask_interrupted_copy` | Recovery | Unresolved run followed by other completed run | Maintenance remains held | Unit | ✅ |
| 25 | `test_malformed_record_holds_maintenance` | Failure | Truncated JSON, missing identity, non-object | Maintenance required, no guessed state | Unit | ✅ |
| 26 | `test_parallel_receipts_remain_individually_readable` | Concurrency | Concurrent receipt appends | Every complete record preserved | Unit | ✅ |
| 27 | `test_completed_activation_with_first_write_is_resolved` | Recovery | Completed activation followed by first write | Startup allowed | Unit | ✅ |
| 28 | `test_migration_builders_preserve_workflow_ownership` | Fixture | Two runs, object and activation marker | Unique identities and correct owning run | Repository | ✅ |
| 29 | `test_migration_health_contains_only_safe_progress` | Contract | Paused run with protected config | Safe bounded health fields, no credentials | Integration | ✅ |
| 30 | `test_verified_activation_downloads_every_online_delta_artifact` | Headline | Real backup, baseline, ingestion, recovery, activation | Every Artifact downloads; Full audit succeeds | E2E | ✅ |
| 31 | Provider pair migration (`tests/contract/modules/storage/test_vault_migration.py`) | Contract | Local→S3, S3→local, S3→S3 | Unicode and large bytes verified; ranges correct | Contract | ✅ |
| 32 | `test_incomplete_native_upload_is_fenced_to_retained_source` | Invariant | Provider-native parts incomplete at cutover | Session fenced; exact original provider retained for abort | Integration | ✅ |
| 33 | `test_completed_native_staging_relocates_encrypted_receipt` | Invariant | Native completion receipt not yet ingested | Destination receipt and encrypted owner remapped atomically | Integration | ✅ |
| 34 | Source cleanup receipt match | Invariant | Full audit, post-activation backup and expired grace | Only receipt-matching source bytes deleted; deletes audited | Integration | ❌ |
| 35 | `test_cleanup_preserves_changed_source_identity` | Failure | Source replaced out of band after activation | Changed/unowned bytes retained with findings | Integration | ✅ |
| 36 | `test_cleanup_waits_for_old_generation_stream` | Concurrency | Old stream survives activation, zero-day grace | Cleanup refused until reader closes | Integration | ✅ |
| 37 | `test_other_vault_namespace_cannot_reuse_backup`, `test_activation_requires_the_original_preflight_archive` | Invariant | Backup from other Vault or replaced archive | Preflight/activation rejected | Integration | ✅ |
| 38 | `test_cleanup_requires_backup_created_after_activation`, headline E2E | Boundary | Only preflight backup available at cleanup | Cleanup rejected until fresh destination backup | Integration/E2E | ✅ |
| 39 | Schema upgrade with existing data | Migration | Existing notification and library rows | Upgrade/downgrade preserve prior rows; fresh/upgrade schema converge | Integration/Contract | ❌ |
| 40 | `test_failed_candidate_never_replaces_source_downloads`, `test_drain_timeout_leaves_source_authoritative` | Failure | Changed candidate or drain timeout | Source stays active; partial destination never served | E2E/Integration | ✅ |
| 41 | `test_unbounded_consumer_read_remains_bounded`, `test_low_bandwidth_never_sleeps_for_more_than_one_quarter_second` | Boundary | Unbounded consumer read, low transfer limit | Read capped at 1 MiB; bounded throttle checkpoints | Unit | ✅ |
| 42 | `test_missing_backup_refuses_migration` | Failure | Missing backup | Backup prerequisite rejects missing archive | Integration | ✅ |
| 43 | `test_full_census_preserves_managed_objects_but_excludes_linked_files` | Invariant | Primary, derived, embedded, capture, staging, linked external | Managed objects copied; external bytes excluded; slot/lease receipts remapped | Integration | ✅ |
| 44 | `test_explicit_retention_never_deletes_source` | Happy path | Activated migration | Indefinite retention/manual cleanup recorded; no source deletion | Integration | ✅ |
| 45 | Security skill review | Process | User explicitly opted out | Omitted by request, not reported as passed | Review | ⏭️ |
| 46 | `test_destination_recovery_respects_post_activation_deletion` | Recovery | Legitimate purge after destination activation | Current catalogue recovered without resurrecting removed Artifacts | Integration | ✅ |
| 47 | `test_destination_recovery_refuses_corrupted_current_artifact` | Failure | Current destination bytes corrupted after write admission | Recovery refuses; maintenance stays held | Integration | ✅ |
| 48 | `test_api_chunk_session_can_finish_after_cutover` | Compatibility | Pending local resumable upload | Existing chunks finalize after migration | Integration | ✅ |
| 49 | `test_discard_deletes_only_receipt_proven_destination_copy` | Cleanup | Baseline copied, source still authoritative | Exact candidate copy removed; source retained | Integration | ✅ |
| 50 | `test_uncertain_destination_copy_is_preserved_on_discard` | Failure | Candidate exists without a durable receipt | Foreign candidate bytes retained with a finding | Integration | ✅ |
| 51 | `test_second_preflight_cannot_create_an_overlapping_workflow` | Concurrency | Existing planned workflow | Second preflight rejected before candidate enrollment | Integration | ✅ |

## Execution evidence

- Focused migration, recovery, journal and transfer tests: **50 passed**.
- Real Local/S3 provider pairs: **3 passed**.
- Real HTTP workflows (successful migration/cleanup and failed candidate): **2 passed**.
- Backup prerequisites and detailed health: **7 passed**.
- Rechecked library transfer and purge regressions: **189 passed**.
- Backend lint and type checking passed; the OpenAPI snapshot adds only the
  migration endpoints/schemas, with no changes or removals to existing contracts.
- Full backend/core/frontend coverage gates are still in progress. This matrix
  does not claim the aggregate gates or remote CI have passed.

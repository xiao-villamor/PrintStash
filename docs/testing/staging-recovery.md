# Staging recovery acceptance matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | retains failed input | Error | Failed uncommitted upload | Bytes and lease remain | Integration | ✅ TestStagingCleanup.test_reconciliation_retains_failed_retry_input |
| 2 | retains refused ZIP input | Error | Refused ZIP inspection | Input remains for retention/discard | Integration | ✅ TestInspectUploadedArchive.test_retains_the_staged_archive_it_refused |
| 3 | releases committed imports | Happy | Completed upload, unexpired lease | Input and lease released | Integration | ✅ TestStagingCleanup.test_reconciliation_releases_completed_upload_before_expiry |
| 4 | protects replaced files | Error | Same path, new identity | Replacement survives, capacity charged | Integration | ✅ TestStagingCleanup.test_release_preserves_a_replacement_file |
| 5 | protects active expired inputs | Edge | Running job, expired lease | Input survives expiry sweep | Integration | ✅ TestStagingCleanup.test_expiry_preserves_active_input |
| 6 | serializes discard/retry | Edge | Two concurrent WAL writers | One coherent ownership result | Integration SQLite | ✅ TestStagingCleanup.test_discard_serializes_against_retry |
| 7 | serializes discard/retry | Edge | Two concurrent PostgreSQL writers | One coherent ownership result | Integration PostgreSQL | ✅ TestStagingDiscard.test_discard_serializes_against_retry |
| 8 | releases owner capacity | Happy | Failed owned ingest | 204, bytes/lease removed | Integration API | ✅ TestDiscardStaging.test_owner_reclaims_retained_capacity |
| 9 | makes discard idempotent | Edge | Repeated discard | 204 again | Integration API | ✅ TestDiscardStaging.test_discard_is_idempotent |
| 10 | denies another user | Error | Different owner | 404, bytes unchanged | Integration API | ✅ TestDiscardStaging.test_other_user_cannot_discard |
| 11 | permits administrator | Happy | Administrator | 204 | Integration API | ✅ TestDiscardStaging.test_admin_can_discard |
| 12 | refuses active work | Error | Running job | 409 | Integration API | ✅ TestDiscardStaging.test_active_job_cannot_be_discarded |
| 13 | refuses uncertain ownership | Error | Replacement at leased path | 409, charged lease remains | Integration API | ✅ TestDiscardStaging.test_uncertain_ownership_cannot_be_discarded |
| 14 | reports retained capacity | Happy | Failed ingest lease | Bytes/count/expiry, no path | Integration API | ✅ TestDiscardStaging.test_status_exposes_retained_capacity |
| 15 | prevents stale retry | Error | Discard then retry | Input unavailable, no queued work | Integration API | ✅ TestDiscardStaging.test_discard_prevents_retrying_missing_input |
| 16 | confirms destructive recovery | Happy | Retained input in Tasks | Confirmation before POST | Frontend | ✅ staged-input-recovery.test.tsx |
| 17 | exposes recovery end to end | Happy | ZIP with unsafe entry | Failed job, confirmed discard, capacity released | Playwright real | ✅ zip-upload.spec.ts: reclaims the retained input of a failed ZIP preparation |
| 18 | refuses replaced retry input | Error | Same path, different receipt | Retry refused | Integration | ✅ TestRetry.test_replaced_input_cannot_be_retried |
| 19 | preserves replacement on cancellation | Error | Cancel with uncertain ownership | Replacement and charged lease remain | Integration | ✅ TestCancel.test_cancellation_keeps_uncertain_ownership |
| 20 | summarizes multiple inputs privately | Edge | Two leases and non-ingest work | Sum/count correct; no paths or unauthorized discard | Integration | ✅ TestStagingViews |

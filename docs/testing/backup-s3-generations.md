# Real S3 physical-generation contracts

Lost publication receipts do not authorize adoption by size and digest alone on
an unversioned remote target. Exact deletion likewise requires an immutable S3
version; an ETag can be shared by a later recreation. The two old success cases
created unversioned buckets while expecting generation-specific recovery and
retirement. Their failures were reproduced against the real service.

Success cases now explicitly enable versioning before publication and assert
real generation identities. Separate unversioned cases assert refusal, absence
of fabricated ownership and preservation of the remote archive bytes. Each
case owns a fresh bucket, so versioning never changes the negative cases. The
provider and production recovery/deletion implementations remain unchanged.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Retry reconciles a write committed before receipt recording | Happy | Real versioned S3; publication response lost | One write; completed retry adopts its physical version and original archive digest | Integration | ✅ `tests/integration/modules/backups/test_backup_runs.py::TestExactReplicaRetry::test_retry_reconciles_a_write_committed_before_receipt_recording` |
| 2 | Refuses lost receipt without a physical generation | Error | Real unversioned S3; publication response lost | Retry refuses; no ownership grant; published archive bytes remain unchanged | Integration | ✅ `tests/integration/modules/backups/test_backup_runs.py::TestExactReplicaRetry::test_refuses_lost_receipt_without_a_physical_generation` |
| 3 | Delete backup removes S3 copy | Happy | Real versioned S3 archive with downloaded cache | Exact archive generation and cache disappear | Contract | ✅ `tests/contract/modules/backups/backup/test_s3.py::TestBackupS3::test_delete_backup_removes_s3_copy` |
| 4 | Refuses deletion without a physical generation | Error | Real unversioned S3; remote-only archive | Unsupported exact deletion raises; original remote digest remains intact | Contract | ✅ `tests/contract/modules/backups/backup/test_s3.py::TestBackupS3::test_refuses_deletion_without_a_physical_generation` |
| 5 | Retry publishes the same surviving archive digest | Edge | Failed remote write; verified local survivor; provider recovers | Retry completes with the original archive SHA256 | Integration | ✅ `tests/integration/modules/backups/test_backup_runs.py::TestExactReplicaRetry::test_retry_publishes_the_same_surviving_archive_digest` |

Both historical backup failures are preserved in the three-case reproduction: **3 failed in 34.40s** (the third is the separately repaired DBOS reset). Initial focused S3 validation: **5 passed in 24.84s**; final identity assertions are validated separately, without adding overlapping counts. No local full, coverage or Deep CI was run.

Final focused selection with physical-version assertions: **5 passed in 11.42s**; Ruff, formatting and whitespace checks pass. This repeats the same five cases and is not added to the initial selection.

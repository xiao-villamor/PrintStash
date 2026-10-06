# Durable batch store boundaries

The batch import requirements in `docs/architecture/background-work.md` require exact writer authority, durable source receipts and canonical Artifact confirmation. Real SQLite, factory-backed Jobs/Files and current execution contexts are used; constraints and fences remain active.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses_another_jobs_batch | Error | Unrelated real Job | Cancellation; zero entries | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_refuses_another_jobs_batch` |
| 2 | refuses_inactive_inbox_owner | Error | Captured/completed inbox | Cancellation; zero entries | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_refuses_inactive_inbox_owner` |
| 3 | refuses_unknown_entry_outcome | Error | Absent key | LookupError; original receipt intact | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_refuses_unknown_entry_outcome` |
| 4 | refuses_invalid_result_page | Error | Bad limit/cursor | ValueError; receipt intact | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_refuses_invalid_result_page` |
| 5 | refuses_confirmation_for_unknown_receipt | Error | Absent receipt ID; real File | LookupError; receipt pending | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_refuses_confirmation_for_unknown_receipt` |
| 6 | refuses_confirmation_for_unpersisted_file | Error | Factory detached File | RuntimeError; receipt pending | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_refuses_confirmation_for_unpersisted_file` |
| 7 | preserves_reconciled_success | Edge | Actual File/confirmed receipt | Same exact IDs/state after repeated reconciliation | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_preserves_reconciled_success` |
| 8 | excludes_claimed_legacy_commit | Edge | Claimed/unclaimed real legacy Files | Only unclaimed candidate returned | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_excludes_claimed_legacy_commit` |
| 9 | records_canonical_confirmation | Edge | Imported/deduplicated flag | Committed receipt exact IDs/state | Integration | ✅ `integration/modules/ingestion/test_batch_store.py::TestStoreBoundaries::test_records_canonical_confirmation` |

## Validation and limits

Affected integration file: **33 passed, 1 deprecation warning in 6.55s** (bounded wall 10.65s). The 17 new cases cover the nine behaviours above; existing tests remain. Ruff check, format and whitespace checks pass. An initial command ran from the backend directory before the intended additions were written and only executed the existing tests; it is not new-contract validation or a production RED result.

The recorded Deep baseline for this owner was 88.66%. No local coverage/full/Deep run was performed, no floor/debt entry changed, and the final measured percentage remains pending the single GitHub Deep run after all owner corrections merge. Real persisted entries and Files always have IDs; this change does not fabricate invalid persisted rows to force those defensive guards. It does not claim complete global qualification.

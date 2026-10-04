# Private ingestion benchmark cleanup

The ingestion benchmark records every sample before withdrawing unfinished work.
A sample deadline bounds observation; it does not cancel an accepted Job. Jobs
may overlap later samples in the same private vault. Cleanup costs are reported
separately and cannot change sample timing or outcomes.

After all samples, `cleanup_private_jobs` lists private Jobs through the public
API (`terminal_limit=0`, `include_system=true`) and cancels each active Job
through the canonical endpoint. A 409 is resolved by reading the Job again:
a terminal Job is a legitimate race; an active Job remains eligible on the
next pass. API/protocol failures remain evidence and prevent a success claim.

An empty list alone is not a barrier. Cleanup holds the existing restore
maintenance gate and lists again, preventing new mutating steps from admission
between observation and shutdown. A new Job requires releasing the gate before
its cancellation endpoint can run, then repeating the barrier. Once held,
cleanup waits for admitted mutating operations and pinned readers to drain.
The native supervisor kills and reaps children before its step releases the
mutation counter.

Every returned observation leaves the gate held, including failures. The
caller retains it throughout TestClient teardown, checks counters again, then
releases it with `end_restore_maintenance`. Timeout or error requires retaining
the private workspace. Job cancellation state is not physical cleanup proof:
DBOS destroy uses a bounded completion wait and leaves running synchronous
executor threads. This helper is exclusively for the trusted, process-local
benchmark vault; never use it against a user installation or shared vault.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | gates an idle vault | Happy | No active private Jobs | Quiescence; new mutations refused until caller releases gate | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_gates_an_idle_vault` |
| 2 | withdraws queued private work | Happy | User/system Jobs plus terminal Job | Active Jobs cancelled; terminal preserved; identifiers/kinds retained | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_withdraws_queued_private_work` |
| 3 | reaps a cancelled native child | Happy | Running step owns real child and pinned lease | Child reaped; step cancelled; counters zero | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_reaps_a_cancelled_native_child` |
| 4 | accepts a terminal cancellation race | Edge | Job finishes before cancel | 409 resolved by GET; no false error or cancellation claim | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_accepts_a_terminal_cancellation_race` |
| 5 | cancels work admitted before the final gate | Edge | Job appears after initial empty list | New Job cancelled before gated quiescence claim | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_cancels_work_admitted_before_the_final_gate` |
| 6 | retains mutation timeout evidence | Error | Admitted operation fails to drain | Positive counter; nonquiescent; gate held | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_retains_mutation_timeout_evidence` |
| 7 | retains reader timeout evidence | Error | Pinned reader remains | active_readers; nonquiescent; gate held | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_retains_reader_timeout_evidence` |
| 8 | retains listing failure evidence | Error | Unavailable endpoint or malformed listing | Bounded error; no false quiescence; gate held | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_retains_listing_failure_evidence` |
| 9 | retains transport failure evidence | Error | Listing transport raises | Transport error retained; gate held | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_retains_transport_failure_evidence` |
| 10 | retains cancel failure evidence | Error | Cancel endpoint unavailable | Job remains queued; no cancellation claim; gate held | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_retains_cancel_failure_evidence` |
| 11 | retries an active cancellation conflict | Edge | 409 recheck returns active Job | Next pass withdraws Job; quiescence succeeds | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_retries_an_active_cancellation_conflict` |
| 12 | rejects invalid cleanup deadlines | Error | Negative/nonfinite deadline | ValueError before API/gate effects | Integration | ✅ `integration/scripts/test_benchmark_cleanup.py::TestCleanupPrivateJobs::test_rejects_invalid_cleanup_deadlines` |

No application shutdown, scheduling, storage, or command contracts change. The CLI
owns retention and post-shutdown verification; its separate suite covers them.

Validation: focused cleanup integration suite: **15 passed in 6.35s**.
The native-child case uses the real supervisor and cancellation API, with
a registered Job definition and production mutation/reader owners. API fault
cases stand in for failures at the HTTP boundary. Initial collection before
the helper existed failed as expected; this is not a behavioral regression
proof. No application production code changed.

# Active mesh cancellation — #259

Cancelling a Job must withdraw its current native execution while preserving the
original Artifact and allowing following work. DBOS step boundaries alone do not
interrupt a synchronous native supervisor. The real derivative Job test failed
before this correction: it completed after its worker deadline instead of
cancelling. Immediate retry is an additional withdrawal of the prior attempt.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | stops active mesh work | Error | Real derivative Job; native worker with child and partial output; cancel or immediate retry | Job cancelled/requeued; no descendants, temporary output or capacity; original intact; following Artifact ready | Integration | ✅ integration/modules/derivatives/test_producers.py::TestMeshCancellation::test_withdrawal_stops_in_flight_native_work |
| 2 | stops active production work | Error | DBOS Job and observed native worker processing a 500,000-face ASCII STL | Public cancellation; native tree gone within 15 s; derivatives cancelled; original SHA preserved; next metadata ready | Production container | ✅ scripts/mesh_resource_gate.py::Gate.cancel_native |
| 3 | withdraws superseded attempt | Edge | Running Job at attempt 2 | Attempt 1 withdrawn; current attempt continues | Integration | ✅ integration/modules/work/test_runner.py::TestActiveAttempt::test_superseded_execution_is_withdrawn |
| 4 | withdraws requeued execution | Edge | Immediate retry requeues the prior attempt | Scoped old execution withdrawn; ordinary queued intent remains | Integration | ✅ integration/modules/work/test_runner.py::TestActiveAttempt::test_requeued_execution_is_withdrawn |
| 5 | releases cancelled admission waiter | Edge | All permits occupied; queued work cancelled | Waiter exits promptly without admission; existing permit retained | Unit | ✅ unit/modules/media/mesh_processing/test_admission.py::TestRenderAdmission::test_cancelled_waiter_releases_without_admission |
| 6 | reaps visual workers | Error | Active embedding/view render; cancellation | No owned native descendants | Integration | ✅ integration/modules/media/test_visual_render.py::TestVisualRender::test_cancellation_reaps_the_worker_tree |
| 7 | reaps streaming workers | Error | Active isolated STL stream; cancellation | Root reaped; descendants absent; temporary directory removed | Integration | ✅ integration/modules/media/test_stl_streaming.py::TestStreamingCancellation::test_cancellation_reaps_owned_workers |
| 8 | refuses withdrawn finished reply | Edge | Successful reply or resource refusal inside polling interval | Cancellation raised before worker outcome accepted; descendants reaped | Integration | ✅ integration/modules/media/test_worker_bootstrap.py::TestCancelledReply::test_withdrawal_before_reply_acceptance_reaps_descendants |
| 9 | permits unscoped operations | Happy | No current Job scope | Ordinary operation continues | Unit | ✅ unit/core/test_cancellation.py::TestCancellationScope::test_unscoped_work_continues |
| 10 | bounds durable polling | Edge | Repeated checkpoints within 200 ms | Durable check count remains bounded | Unit | ✅ unit/core/test_cancellation.py::TestCancellationScope::test_throttles_durable_checks |
| 11 | latches withdrawal | Edge | Intent changes after cancellation observed | Same execution remains stopped | Unit | ✅ unit/core/test_cancellation.py::TestCancellationScope::test_latches_withdrawal |
| 12 | restores outer scope | Error | Nested scope raises | Outer cancellation restored; later unscoped work continues | Unit | ✅ unit/core/test_cancellation.py::TestCancellationScope::test_restores_outer_scope_after_exception |
| 13 | isolates concurrent scopes | Edge | Two concurrent operations with different intent | Only withdrawn execution stops | Unit | ✅ unit/core/test_cancellation.py::TestCancellationScope::test_isolates_concurrent_operations |
| 14 | checks final result forcibly | Edge | Withdrawal during throttled interval | Forced checkpoint refuses output | Unit | ✅ unit/core/test_cancellation.py::TestCancellationScope::test_forces_final_result_check |

| 15 | parses repeated factory facets | Happy | One/two ASCII STL facets | Exact facet count and bounds through production parser | Unit | ✅ unit/modules/media/test_stl_preview_worker.py::TestReadAscii::test_reads_repeated_factory_facets |

| 16 | refuses superseded step writes | Edge | Cancel/retry or a newer running attempt before step starts | Cancelled outcome; no stale output written | Integration | ✅ integration/modules/work/test_runner.py::TestActiveAttempt::test_superseded_step_cannot_write |

Fake idle worker tests use 256 MiB to include subprocess/fork coverage startup.
The sudden-allocation test retains its 128 MiB hard limit. Aggregate-memory
reaping uses explicit 256 MiB allocations in both processes, a 512 MiB per-process
ceiling and a smaller 480 MiB tree RSS budget. No production budget changes.
Cancellation timing starts after the test observes native work; coverage startup
is not part of the cancellation latency.

Run the same production gate documented in [mesh-regression.md](mesh-regression.md).
Its 1/4 GiB amd64/arm64 Deep CI matrix includes active cancellation. Retention RSS
samples begin after the large cancellation input so cold allocation is not
mistaken for accumulation.

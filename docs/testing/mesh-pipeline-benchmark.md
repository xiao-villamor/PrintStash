# Complete mesh benchmark measurement paths

The frozen corpus runner measures disposable native workers separately from
upload-to-visible work through the production application and DBOS. All attempts
and their costs survive refusals. Each observation identifies its source bytes,
recipe, environment, repetition and measurement boundary. Successful observations
never stand in for failed samples.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | `test_reports_real_worker_costs` | Happy | Frozen cube, disposable worker | Valid image hash, phases, positive parent time and sampled RSS | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_reports_real_worker_costs` |
| 2 | `test_preserves_refused_worker_sample` | Error | Frozen truncated STL | No success image, explicit reason, elapsed costs retained | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_preserves_refused_worker_sample` |
| 3 | `test_describes_worker_measurement_boundaries` | Edge | Worker CLI | Source hash/recipes/environment, fresh worker and uncontrolled OS cache explicit | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_describes_worker_measurement_boundaries` |
| 4 | `test_refuses_invalid_repetitions` | Error | Zero or negative runs | CLI exit 2 before work | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_refuses_invalid_repetitions` |
| 5 | `test_refuses_unknown_corpus_selector` | Error | Case absent from frozen manifest | CLI exit 2 before work | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_refuses_unknown_corpus_selector` |
| 6 | `test_measures_real_derivative_availability` | Happy | Cube uploaded into fresh private vault | Accepted Job, committed Artifact, metadata READY, fetched valid thumbnail | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_measures_real_derivative_availability` |
| 7 | `test_retains_refused_derivative_observations` | Error | Truncated STL uploaded | Original committed, derivative failure retained without fake visible timestamp | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_retains_refused_derivative_observations` |
| 8 | `test_distinguishes_source_presence_from_artifact_reuse` | Edge | Same input uploaded twice into new Models in the same vault | Existing source is marked; Artifact reuse reported from actual identity | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_distinguishes_source_presence_from_artifact_reuse` |
| 9 | `test_preserves_flow_deadline_costs` | Error | Zero deadline after accepted upload | Timeout observation with costs; no fabricated completion | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_preserves_flow_deadline_costs` |
| 10 | `test_counts_every_observation` | Edge | Mixed completed/refused samples | All sample indices and failures present; no qualified percentile claim | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_counts_every_observation` |
| 11 | `test_preserves_child_engine_evidence` | Happy | Engine returns duration and peak RSS | Exact measured child values retained beside supervisor | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_preserves_child_engine_evidence` |
| 12 | `test_reports_observed_output_format` | Edge | Worker returns a PNG | Actual format/dimensions/hash retained without claiming canonical storage | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_reports_observed_output_format` |
| 13 | `test_preserves_geometry_refusal_with_preview` | Error | Valid preview, geometry refused | Geometry cause retained instead of invented fallback | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_preserves_geometry_refusal_with_preview` |
| 14 | `test_retains_unexpected_worker_failure` | Error | Worker raises unexpected exception | Failed observation with elapsed cost; no phase or image fabricated | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_retains_unexpected_worker_failure` |
| 15 | `test_preserves_native_deadline_cost` | Error | Supervisor times out | Timeout outcome with actual supervision retained | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_preserves_native_deadline_cost` |
| 16 | `test_refuses_invalid_deadline` | Error | Negative, NaN, infinite deadline | CLI exit 2 before processing | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_refuses_invalid_deadline` |
| 17 | `test_isolates_installation_configuration` | Edge | Inherited VAULT variables and local .env point outside private vault | Real ingestion succeeds in private defaults; external installation untouched | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_isolates_installation_configuration` |
| 18 | `test_executes_expanded_corpus` | Happy | v2 selected frozen case | v2 manifest/hash preserved in completed observation | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_executes_expanded_corpus` |
| 19 | `test_refuses_incompatible_corpus_profiles` | Error | Full/download requested for v1 | CLI exit 2 before materialization | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_refuses_incompatible_corpus_profiles` |
| 20 | `test_pins_child_configuration_across_directories` | Edge | Child starts next to .env with different width/budget | Actual native output matches private parent dimensions | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestWorkerBenchmark::test_pins_child_configuration_across_directories` |
| 21 | `test_classifies_native_execution_failure` | Error | Typed worker/storage/renderer failure | Failed outcome retains reason and supervision | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_classifies_native_execution_failure` |
| 22 | `test_preserves_native_policy_refusal` | Error | Invalid input/resource limit | Refused outcome with explicit cause | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_preserves_native_policy_refusal` |
| 23 | `test_rejects_missing_refusal_cause` | Error | No image or reported cause | Failed observation preserves available engine costs | Unit | ✅ `unit/scripts/test_benchmark_native.py::TestMeasureWorker::test_rejects_missing_refusal_cause` |
| 24 | `test_classifies_operational_failures` | Error | Worker/storage/renderer or unknown failure code | Failed outcome instead of policy refusal | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_classifies_operational_failures` |
| 25 | `test_preserves_input_refusals` | Error | Invalid source/unsupported geometry/resource limit | Refused outcome | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_preserves_input_refusals` |
| 26 | `test_preserves_native_timeout` | Error | Explicit native timeout | Timeout outcome | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_preserves_native_timeout` |
| 27 | `test_preserves_capability_unavailability` | Edge | Skipped/cancelled/disabled derivative | Refused availability without invented execution failure | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_preserves_capability_unavailability` |
| 28 | `test_prioritizes_operational_failure` | Error | Policy refusal/timeout plus execution failure | Failed outcome has priority | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_prioritizes_operational_failure` |
| 29 | `test_rejects_nonterminal_unavailability` | Error | READY/pending/running/queued state | Explicit validation error | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_rejects_nonterminal_unavailability` |
| 30 | `test_rejects_empty_observations` | Error | Empty observations | Explicit validation error | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_rejects_empty_observations` |
| 31 | `test_retains_source_probe_failure` | Error | Real source table unavailable | Failed sample retains query cost; source presence/acceptance remain unknown | Integration | ✅ `integration/scripts/test_benchmark_ingestion.py::TestMeasureIngestion::test_retains_source_probe_failure` |
| 32 | `test_drains_private_work_before_workspace_removal` | Happy | Completed upload into private vault | Cleanup gate held through teardown; zero owners; workspace removed | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_drains_private_work_before_workspace_removal` |
| 33 | `test_drains_timed_out_private_work` | Error | Accepted upload reaches observation deadline | Unfinished work cancelled/reaped after samples; sample timeout preserved | E2E | ✅ `e2e/test_mesh_pipeline_benchmark.py::TestIngestionBenchmark::test_drains_timed_out_private_work` |
| 34 | `test_retains_workspace_when_cleanup_fails` | Error | Real private ingestion; canonical cleanup reports uncertainty | JSON retains cleanup error and owned workspace instead of deleting it | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestPrivateWorkspace::test_retains_workspace_when_cleanup_fails` |
| 35 | `test_retains_workspace_when_bootstrap_fails` | Error | Application bootstrap raises before observations | Failure report retains private workspace for inspection/recovery | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestPrivateWorkspace::test_retains_workspace_when_bootstrap_fails` |
| 36 | `test_rejects_active_mutations_after_teardown` | Error | Actual mutation counter remains positive after client teardown | Postcheck rejects quiescence; workspace must remain owned | Integration | ✅ `integration/scripts/test_benchmark_ingestion.py::TestObserveTeardown::test_rejects_active_mutations_after_teardown` |
| 37 | `test_rejects_active_readers_after_teardown` | Error | Actual generation reader remains pinned after client teardown | Postcheck rejects quiescence; workspace must remain owned | Integration | ✅ `integration/scripts/test_benchmark_ingestion.py::TestObserveTeardown::test_rejects_active_readers_after_teardown` |
| 38 | `test_prioritizes_native_timeout` | Error | Metadata input/resource refusal plus thumbnail timeout | TIMEOUT takes priority over REFUSED without inventing an execution failure | Unit | ✅ `unit/scripts/test_benchmark_pipeline_contracts.py::TestUnavailableDerivativeOutcome::test_prioritizes_native_timeout` |
| 39 | `test_retains_workspace_when_teardown_fails` | Error | Lifespan teardown raises after samples/cleanup | Failure report retains private workspace instead of deleting uncertain owners | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestPrivateWorkspace::test_retains_workspace_when_teardown_fails` |
| 40 | `test_closes_lifespan_after_sample_exception` | Error | Sample orchestration raises inside an entered lifespan | Cleanup/gate ordering still closes the application lifespan | Integration | ✅ `integration/scripts/test_bench_mesh_pipeline.py::TestPrivateWorkspace::test_closes_lifespan_after_sample_exception` |
| 41 | `test_preserves_cleanup_failure_after_owners_drain` | Error | Cleanup failed; physical owners have since drained | Latest counters are zero; failure/error evidence still prevents removal | Integration | ✅ `integration/scripts/test_benchmark_ingestion.py::TestObserveTeardown::test_preserves_cleanup_failure_after_owners_drain` |

Test paths are relative to `backend/tests/`. All 41 behaviours have verified
focused evidence: 186 tests across the initial owners; 21 cleanup/flow tests;
28 fault/contract tests after fixing probe imports; and 16 final lifecycle tests
including secondary cleanup exceptions. These are overlapping selections, not
an aggregate count. This matrix covers CLI orchestration. The independently
verified cleanup-owner matrix and real native-child evidence are in
[private ingestion benchmark cleanup](mesh-benchmark-cleanup.md). Broader gates
are recorded with the pull request; no performance qualification is claimed.


## Telemetry capture preconditions

Telemetry is logged at INFO. Its unit assertions explicitly enable INFO for the
owner logger and pytest capture, so another test or a WARNING-level suite setting
cannot silently suppress the record under test. Production logging is unchanged.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `test_keeps_costs_in_log_message` | Edge | Supervision record; suite capture defaults to WARNING | Captured INFO JSON preserves execution identity, elapsed time and tree RSS | Unit | ✅ `unit/modules/media/test_mesh_observability.py::TestRecordSupervision::test_keeps_costs_in_log_message` |
| 2 | `test_correlates_logged_phases` | Edge | Load phase; suite capture defaults to WARNING | Captured INFO JSON correlates execution identity and known phase measurements | Unit | ✅ `unit/modules/media/test_mesh_observability.py::TestRecordPhases::test_correlates_logged_phases` |

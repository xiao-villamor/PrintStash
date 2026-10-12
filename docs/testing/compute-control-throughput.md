# Remaining compute throughput bottlenecks

This follow-up measures the shared CPU/GPU ingestion path after the codec fix. The previous matched run was **2253.508 seconds UTC on GPU versus 2186.634 seconds on CPU**. GPU execution was only 28.332 accumulated seconds; loading, admission, process lifetime and control coordination dominated. These overlapping stage sums are not an additive wall-time breakdown.

## Changes and falsifiable probes

- Runtime source/dependency fingerprinting is cached per interpreter/PID. Each connection still hashes current policy values and compares the owner's profile. A fresh process reattests deployed sources. All owners must restart on deployment; live source edits are unsupported.
- Explicit supervisor warm-up can use its 30-second startup budget. Ordinary readiness probes retain five seconds, and work requests retain their original deadline. This addresses cold-start termination before GPU discovery completed; the complete run will verify whether cold recovery disappears.
- OBJ preflight retains strict syntax/reference validation and the 8 MiB/4096-byte bounds. It checks a 250 ms thread-CPU allowance and one-second wall watchdog every 128 lines and at EOF. A common triangular face avoids the general three-channel tokenizer. Unknown/changed/malformed sources still claim the full native pool. The CPU allowance is deliberately larger than the former 100 ms wall allowance; it spends bounded counting work to avoid expensive whole-pool serialization.
- Native admission rechecks kernel descriptor locks every scan but decodes only changed live receipts. Device/inode, size, mtime and ctime invalidate cached data; dead receipts are evicted. FIFO, priority aging, resource weights and guardian custody remain unchanged.
- STL loading installs the canonical reader's exact bounds in trimesh's tracked-array cache after creating the mesh. This avoids gathering another complete vertex array. Vertex mutation invalidates the cache normally.
- Container builds precompile application bytecode. Parsers still execute in fresh guarded processes; no native parser state is pooled. Imports remain the main residual small-worker startup cost.

The agent ran every shell/Git operation as Linux `local` inside WSL. An initial `python -m scripts.bench_mesh_pipeline --mode worker --runs 3 --case cube-binary.stl` observed 3019.23, 1855.60 and 1855.41 ms. Loading took 1675.85, 723.98 and 678.71 ms for this 684-byte source; a separate import trace attributed 810.76 ms to trimesh. The first sample overlapped a diagnostic import probe, so this is diagnosis rather than a clean before/after speed claim.

| Component probe | Before | After | Interpretation |
|---|---:|---:|---|
| 200 fingerprint calls / four threads, accumulated elapsed | 20.795 s | 0.550 s | Includes initial candidate attestation; repeated-call median 104.583 ms → 0.0122 ms |
| Countable OBJ sources / 486, four threads | 347 | 451 | Same count for every source measured by both arms |
| OBJ preflight elapsed / thread CPU | 7.105 / 7.502 s | 15.865 / 17.674 s | Increased bounded counting avoids 104 full-pool claims in this probe |
| 1000 ledger scans with four live receipts | 0.2803 s | 0.1651 s | 41.1% less scan time; custody still checked every time |

The probes are single passes on a shared WSL host. They establish component behavior, not full-pipeline throughput. The previous source snapshots come from `68d74f78`; these are independent of the timed 1000-source comparisons.

## Validation

Focused compute/admission/worker/handoff selection: **389 passed**. Ruff and Pyright: **0 errors**. Full backend gate and the immutable matched CPU/GPU comparison are in progress; no new overall speedup is claimed yet.

## Coverage matrix

Paths below are relative to `backend/tests/`. Existing boundary/refusal, source-identity, cancellation, process-tree custody and CPU recovery tests remain in the broader gates.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | reuses the deployed runtime identity | Happy | Repeated control requests | Same identity without source rereads | Unit | ✅ `unit/runtime/compute/test_discovery.py::TestProcessIdentity::test_reuses_the_deployed_runtime_identity` |
| 2 | rechecks identity in a new process | Edge | PID changes | New process obtains current identity | Unit | ✅ `unit/runtime/compute/test_discovery.py::TestProcessIdentity::test_rechecks_identity_in_a_new_process` |
| 3 | retains mutable policy identity | Edge | Memory policy changes | Profile hash differs | Unit | ✅ `unit/runtime/compute/test_discovery.py::TestProcessIdentity::test_retains_mutable_policy_identity` |
| 4 | honors the explicit startup deadline | Happy | Status with 30 second allowance | Connect receives full allowance | Unit | ✅ `unit/runtime/compute/test_client.py::TestExchangeDeadline::test_honors_the_explicit_startup_deadline` |
| 5 | retains the request work deadline | Edge | Inference deadline earlier than outer limit | Earlier deadline wins | Unit | ✅ `unit/runtime/compute/test_client.py::TestExchangeDeadline::test_retains_the_request_work_deadline` |
| 6 | cools an expired startup | Error | Warm-up times out | CPU cooldown active | Unit | ✅ `unit/runtime/compute/test_client.py::TestExchangeDeadline::test_cools_an_expired_startup` |
| 7 | counts plain OBJ triangles | Happy | Positive/negative triangular indices | Weighted claim | Integration | ✅ `integration/modules/media/test_native_budget.py::TestEstimateSources::test_counts_plain_obj_triangles` |
| 8 | bounds OBJ preflight CPU | Error | CPU budget expires | Whole-pool claim | Integration | ✅ `integration/modules/media/test_native_budget.py::TestEstimateSources::test_bounds_obj_preflight_cpu` |
| 9 | counts slower bounded text | Edge | Wall elapsed exceeds old limit, CPU below bound | Weighted claim | Integration | ✅ `integration/modules/media/test_native_budget.py::TestEstimateSources::test_counts_slower_bounded_text` |
| 10 | observes changed live admission receipts | Edge | Live receipt rewritten same size | Changed state is observed | Integration | ✅ `integration/runtime/test_native_admission.py::TestLocalResourcePool::test_observes_changed_live_admission_receipts` |
| 11 | rejects corrupted cached receipts | Error | Live receipt corrupted | Admission fails closed | Integration | ✅ `integration/runtime/test_native_admission.py::TestLocalResourcePool::test_rejects_corrupted_cached_receipts` |
| 12 | reclaims closed cached receipts | Edge | Owner closes last descriptor | Capacity becomes available | Integration | ✅ `integration/runtime/test_native_admission.py::TestLocalResourcePool::test_reclaims_closed_cached_receipts` |
| 13 | ships compiled worker application code | Happy | Runtime Dockerfile | Compile step follows application copy | Repo | ✅ `repo/test_docker_entrypoint.py::TestWorkerBytecode::test_ships_compiled_worker_application_code` |
| 14 | starts dependencies under hard ceiling | Error | Guarded child allocation | Hard ceiling installed before imports | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_native_dependencies_start_under_hard_ceiling` |
| 15 | retains full geometry with smaller previews | Happy | Uploaded STL with 512-pixel width | Original geometry retained; smaller preview published | E2E | ✅ `e2e/test_ingest.py::TestMetadata::test_retains_full_geometry_with_smaller_previews` |
| 16 | reuses canonical STL bounds | Happy | Materialized independent facets | Exact bounds without another gathered vertex array | Integration | ✅ `integration/modules/media/mesh_loading/test_stl_outcomes.py::TestSTLBounds::test_reuses_canonical_stl_bounds` |
| 17 | invalidates bounds after vertex edits | Edge | Loaded mesh vertices change | New bounds reflect edited geometry | Integration | ✅ `integration/modules/media/mesh_loading/test_stl_outcomes.py::TestSTLBounds::test_invalidates_bounds_after_vertex_edits` |

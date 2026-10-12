# Remaining compute throughput bottlenecks

This follow-up measures the shared CPU/GPU ingestion path after the codec fix. The previous matched run was **2253.508 seconds UTC on GPU versus 2186.634 seconds on CPU**. GPU execution was only 28.332 accumulated seconds; loading, admission, process lifetime and control coordination dominated. These overlapping stage sums are not an additive wall-time breakdown.

## Changes and falsifiable probes

- Runtime source/dependency fingerprinting is cached per interpreter/PID. Each connection still hashes current policy values and compares the owner's profile. A fresh process reattests deployed sources. All owners must restart on deployment; live source edits are unsupported.
- Explicit supervisor warm-up can use its 30-second startup budget. Ordinary readiness probes retain five seconds, and work requests retain their original deadline. This addresses cold-start termination before GPU discovery completed; the completed 1000-source run used GPU for all four cold inputs without fallback.
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

## Completed 1000-source run

The current GPU execution took **3004.626 seconds UTC (50.08 minutes)**, versus **2253.508 seconds (37.56 minutes)** for the previous optimized GPU execution and **2186.634 seconds (36.44 minutes)** for its matched CPU arm. The current run is slower overall. These historical comparisons use different application revisions, tracing detail and uncontrolled host conditions; they do not establish a current-version CPU/GPU speedup or isolate the cause of the regression. At the user's request, the repeated CPU run was stopped and is excluded from the results. The completed follow-up below uses 250 sources in GPU-auto mode, without another CPU run.

The dedicated codec slot, quality-85/method-2 lossy WebP and 512×384 benchmark profile are retained. The runtime diff against the previous completed optimization changes seven files: startup/profile identity, native admission, OBJ preflight, STL bounds and application bytecode. It does not remove the codec or rendering changes.

Current runtime source: `d63a6af2cbe6baf09beebe71eac71ba3dc2fd678`; tracer: `cc128ab5d124860db6d7d408be9a8691533e517b`. The immutable image is `sha256:3ce6601be633f91433f74ca46cc06069b903ed864564947e7ed6e08237d24f24`. The previous measured runtime is `20d60f8b`. Both runs use the same immutable 1000-source cache, four upload/pending-ingestion slots and a continuous 40-unfinished-Artifact window after the first four cold inputs. All execution and control ran as Linux `local` inside WSL; no Windows timing subprocess was used. GPU and CPU test containers are stopped.

All **1000 originals** matched SHA-256 and size. All **1000 metadata rows** exactly match the previous result across nine authoritative geometry/state fields: **973 valid results and the same 27 invalid sources**. All **973 thumbnails** decode as 512×384 lossy RGBA WebP, have nonempty alpha, transparent corners and a clear image border. They total **8,836,144 bytes**. The R2 request ledger is unchanged; the run issued zero R2 acquisition requests. Machine-readable results are in [the sanitized summary](compute-control-1000-summary.json).

| Accumulated stage / resource | Previous optimized GPU | Current GPU | Interpretation |
|---|---:|---:|---|
| Broker connect | 821.701 s | 93.450 s | Repeated runtime attestation no longer dominates control calls |
| Render exchange | 379.676 s | 158.264 s | Less control-path delay |
| Codec queue | 35.085 s | 51.128 s | Dedicated codec remains independent; queue is still small compared with parsing |
| Mesh load | 1332.182 s | 1745.648 s | Increased cost; requires controlled diagnosis |
| Child supervision | 3435.837 s | 4300.278 s | Overlaps load, measurements and worker lifecycle |
| Native admission wait | 1954.980 s | 2233.541 s | RAM-weighted admission still limits throughput |
| Full-pool native claims / 1000 | 184 | 152 | Fewer conservative claims, but still frequent |
| Mean admitted native slots | 1.63 | 1.53 | Four configured slots are not four continuously active parsers |
| Cgroup RAM peak | 2945.8 MiB | 3145.6 MiB | No OOM events in the current run |

Stage sums overlap across processes and nested calls; they are not an additive breakdown of elapsed time. WSL monotonic publication time was **2810.008 seconds**, while UTC was **3004.626 seconds**. Both clocks are preserved without correction. Thread/process CPU counters are separate observations, not a substitute for either elapsed clock.

### Where the time went

- Fresh trimesh imports alone sum to **1503.987 seconds**, with a median **1.414 seconds** per worker. Mesh loading totals 1745.648 seconds. Parsers remain disposable and start under their hard memory ceiling; application bytecode precompilation does not eliminate numeric dependency imports. An alternating three-pair standalone image probe observed substantial overlap: previous image 1.596–1.871 s, current image 1.556–2.200 s, both trimesh 5.1.1. This probe has no pipeline/tracer and is not a throughput result.
- Native admission made **42,747 coordinator acquisitions**, accumulating **708.077 seconds waiting to enter** and **432.820 seconds inside** the coordinator. Entry time also includes pre-lock cancellation checks, so it is not pure kernel flock wait. There were **37,438 memory-blocked polls**, zero slot-blocked polls and 3309 polls denied despite sufficient free capacity (including FIFO/configuration ordering). Exact custody and conservative RAM estimates remain the limiting gates.
- The tracer observed **46,781 actual cancellation probes**, summing to **883.450 seconds elapsed / 193.470 seconds thread CPU**. Throttled fast checks do not write trace records. These checks protect publication authority; removing them wholesale is not an optimization.
- GPU render-batch execution sums to **39.270 seconds**. Host GPU submission calls sum to 10.616 seconds, map/fence waits to **4.760 seconds**, and mapped reads to **0.071 seconds**. These are nested CPU-observed API spans, not calibrated GPU timestamps.
- The application socket trace recorded **2.020 GB sent** in 52.209 accumulated socket-call seconds (36.89 MiB/s) and **2.025 GB received** in 194.833 seconds (9.91 MiB/s). Receive duration includes waiting for producers, request handling and timeout polling. This is application IPC, not PCIe bandwidth; send/receive observations cover opposite ends of the same traffic and must not be added as independent bytes. Four send and 748 receive timeout events were retried by the protocol; these are not final request failures.
- The first four inputs all used GPU, with zero fallback; broker startup took **7.517 seconds**. The old five-second control limit could not cover that cold startup. Across the complete run, 959 retained frames completed codec handoff with zero handoff CPU recovery. Fourteen valid previews used other/direct CPU paths; traces record eight `ComputeUnavailable` connection errors and six packet `ValueError` refusals. The final broker reports only its latest lifetime after an idle restart, so its 778-frame counter is not the whole-run total.
- Sampled maximum combined RSS by role was **661.5 MiB API**, **978.2 MiB mesh/guardian processes**, and **522.2 MiB broker**. The application child-tree peak was 994.7 MiB. Per-process I/O is unavailable on this container, so cgroup I/O counters are retained instead. Process counts include guardians and must not be called admitted parser concurrency.
- The physical GPU peaked at **1971 MiB**, including unrelated host use. It is not dedicated application VRAM. The adapter exposes 8151 MiB; the broker's 1 GiB logical budget and 48 MiB per-model packet bound are different limits. The measured queue and import costs provide no justification for blindly enlarging that packet bound.

### Scheduler stall retained in the result

There was a **307.393-second monotonic interval with no Job state changes** after the last ingestion completion preceding the pause. Resource samples continued normally, with no long native or GPU operation spanning the interval. The DBOS history shows no new mesh reconciliation pass from 10:04:38.844 to 10:10:24.464 UTC; a later scheduled pass resumed processing. The broker became idle, exited and restarted later.

This implicates discovery/wake-up coordination, but the lost-wake-up mechanism has not been proven. It is not claimed fixed, and its duration has not been subtracted from the result. Read-only cursor observation was added after the pause and did not capture a recurrence. The full-run regression cannot be attributed entirely to this one pause or entirely to the host without the controlled follow-up.

## Completed 250-source diagnosis

The fixed subset contains **130 STL, 111 OBJ and 9 STEP** sources, totaling **808,880,155 bytes**. Its selection digest is `2780f43fa04318c7c8ebbc0b127e250f5887e0b3f4382f2d8800e4d72ccd3824`. It retains the same first four cold inputs and selects 246 evenly spaced entries from the remaining immutable cache. All arms use four upload/pending slots, a 40-unfinished-Artifact window, 512×384 output, lossy quality-85/method-2 WebP and GPU-auto preview policy. The internal supersampling factor remains two: 1024×768 before GPU finalization. No performance-qualification receipt is issued.

The arms ran sequentially **old → new → new-light**, once each, with separate fresh data directories and the same current resource, Job-state, cursor and WSL host observers. `old` uses immutable image `sha256:9356394e28be8343822b74620d451d381c108a51e38554d92f100784032739ec` / runtime `20d60f8b`; the other two use the current immutable image above. Both traced arms mount tracer `cc128ab5`; `new-light` disables that detailed tracer but retains the external observers and ordinary application telemetry. No additional CPU arm ran.

| 250-source result | Previous runtime, traced | Current runtime, traced | Current runtime, detailed trace off |
|---|---:|---:|---:|
| Publication, UTC seconds | 626.608 | 650.899 | 739.455 |
| Publication, WSL monotonic seconds | 582.315 | 597.779 | 681.963 |
| First four inputs, UTC seconds | 19.582 | 10.482 | 12.158 |
| GPU completions after first four | 0 | 4 | 4 |
| Valid thumbnails / retained invalid sources | 238 / 12 | 238 / 12 | 238 / 12 |
| Exact original SHA/size matches | 250 | 250 | 250 |
| Cgroup RAM peak, MiB | 2064.3 | 1983.9 | 1999.1 |
| Cgroup CPU, accumulated seconds | 1671.820 | 1864.560 | 2086.923 |
| Full native-pool claims | 60 | 53 | Uninstrumented |
| Mean admitted native slots | 1.53 | 1.67 | Uninstrumented |
| GPU batch sizes 1 / 2 / 3 models | 213 / 8 / 1 | 217 / 8 / 1 | 229 / 2 / 1 |

**There is still no end-to-end speedup:** current traced runtime is 3.9% slower by UTC / 2.7% by monotonic time than the previous traced runtime. Disabling detailed tracing did not improve this pass; it was another 13.6% slower by UTC. This does not prove tracing is free or beneficial. Fixed order, one repetition and a shared host cannot isolate a small code effect or quantify tracing overhead reliably. The old arm also falls back to CPU for its cold first four inputs; the new arms keep those on GPU. None of these results is a pure GPU-only execution of every source.

| Accumulated stage, overlapping | Previous traced | Current traced | Current without detailed trace |
|---|---:|---:|---:|
| Broker connect | 216.213 s | 17.008 s | Uninstrumented |
| Render exchange | 103.422 s | 42.980 s | Uninstrumented |
| Mesh loading | 343.145 s | 414.011 s | 435.046 s |
| Trimesh import, included in loading | 278.469 s | 332.967 s | Uninstrumented |
| Child supervision | 889.988 s | 996.738 s | 1075.646 s |
| Native admission wait | 721.976 s | 698.413 s | Uninstrumented |
| Native coordinator entry / hold | 171.400 / 104.980 s | 206.708 / 110.773 s | Uninstrumented |
| Native coordinator acquisitions | 17,656 | 15,024 | Uninstrumented |
| Codec queue | 8.431 s | 10.687 s | 10.612 s |

The connection improvement and reduced conservative claims survive this smaller comparison. They are outweighed by increased load/import and worker time in this pass. Current Trimesh import median/p95 is **1.197 / 1.980 s**, versus **1.112 / 1.349 s** previously. Higher allowed concurrency can also change contention; this run does not establish that as the cause. No numeric dependency version or parser-pooling change was introduced. Increasing the device budget alone would not remove the measured CPU import and admission costs.

**GPU feeding remains sparse:** the current traced arm had **217 singleton batches out of 226 (96.0%)**, eight two-model batches and one three-model batch. The no-detailed-trace arm had 229 singleton batches out of 232. No batch exceeded three models. Although the renderer accepts eight total views, the four-slot host handoff window and RAM-weighted preparation constrain how many models can become ready. The broker also synchronously maps each batch's readback before executing its next batch. [The Spanish pipeline diagrams and explanation](compute-pipeline-explained.es.md) distinguish current behavior from a proposed compact-loader / multiple-in-flight-batch design; that proposal is not implemented by this follow-up.

All three arms verify **250 original SHA/size pairs**, all **250 metadata rows** across the same nine fields, **238 valid geometry results**, and identical terminal contracts with the historical full-run subset. Every one of the **714 images** decodes as lossy 512×384 RGBA with nonempty alpha, transparent corners and a clear border. Current traced and light outputs both total **2,277,410 bytes**, versus 2,278,538 for the old arm, which used different CPU recovery paths. This is not a claim of byte identity across renderers. There are zero OOM events and zero R2 acquisition requests; source ledgers are unchanged. All benchmark containers are stopped.

The 307-second scheduler pause did **not recur**: longest intervals between observed Job changes were 9.63, 10.27 and 9.65 monotonic seconds. This narrows the reproduction evidence but does not fix or explain the earlier pause. WSL mean one-minute load was 4.00, 5.56 and 5.08; sampled WSL CPU busy fractions were 32.5%, 37.1% and 36.1%. These include the application and other Linux activity, not Windows-host contention. They do not isolate the cause of the slowdown.

Paired resource samples confirm that UTC and monotonic clocks disagree during all three arms, by approximately **44.3, 53.1 and 57.5 seconds** over the sampled intervals. Their median interval ratios are approximately one, with maximum UTC sampling gaps of 3.03, 3.20 and 3.35 seconds. Thus neither a constant conversion factor nor one long missing sample explains the difference. The mechanism is unproven; both clocks remain published without correction. The 1000-source clock audits are retained too.

[Machine-readable 250-source stages, percentiles, resource/lock/transfer counters, image audits and clock audits](compute-control-250-summary.json) contain sanitized aggregates, not source keys, credentials or private input names. The raw observations and methodology scripts remain in the local benchmark cache.

## Validation

Focused compute/admission/worker/handoff selection: **389 passed**. Ruff and Pyright: **0 errors**. The unrestricted full backend run was interrupted after **1111 passed, one failure and one collection error**. Combined unit/contract collection exposed missing package markers on the pre-existing `compute_render` packet test directories; both markers are now present. The DBOS dedupe case `TestIdentity::test_a_dedupe_key_is_free_again_once_its_execution_ends[dbos]` unexpectedly returned `EXISTING`. It passed on isolated recheck; this is a recorded intermittent failure, not claimed fixed. The combined packet/STL/dedupe recheck passed **54 tests**. The **full lane over affected paths passed 620 tests** (130.28 seconds), including compute/media, admission, process containment, DBOS contracts and ingestion. Diagnostic tracing passed **7 tests**. The immutable 1000-source GPU run is complete. The repeated CPU arm was stopped at the user's request; its partial results are excluded. The previous completed CPU run remains a historical reference. The three 250-source GPU-auto diagnostic arms are complete, with 714 image audits and exact metadata/original verification; the end-to-end regression remains unresolved. Required PR CI supplies the exhaustive disjoint ordinary-suite gate. No new overall speedup is claimed yet.

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
| 18 | omits throttled checkpoint trace writes | Edge | One query followed by a throttled check | Exactly one diagnostic span | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestCancellationTrace::test_omits_throttled_checkpoint_trace_writes` |
| 19 | traces cancellation without swallowing it | Error | Withdrawal callback returns true | Cancellation propagates with typed error span | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestCancellationTrace::test_traces_cancellation_without_swallowing_it` |

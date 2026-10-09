# Portable compute validation

This matrix separates implemented software contracts from outstanding physical qualification. Tests using fake execution or llvmpipe do not establish hardware support. Paths below are relative to backend/tests unless marked core.

| # | Behaviour | Category | Precondition / input | Observable outcome | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Ingest without GPU qualification | Happy | cpu/auto, STL upload | Requested metadata and thumbnail ready; original bytes unchanged | E2E | ✅ e2e/test_ingest.py::TestComputePlacement::test_publishes_mesh_outputs_without_gpu_qualification |
| 2 | Reject software adapters | Edge | CPU adapter enumeration | software_adapter before device creation | Unit | ✅ unit/runtime/compute/test_discovery.py::TestDiscovery::test_refuses_software_before_device_creation |
| 3 | Preserve frozen-corpus GPU rendering | Happy | Qualified physical hardware, frozen corpus | Exact mask, RGBA delta ≤8, protected parts, repeatability | Integration | ❌ Physical corpus qualification pending; no eligible receipt shipped |
| 4 | Preserve canonical CPU visual inputs | Happy | Existing analytical mesh fixtures | Frozen visual hashes unchanged | Integration | ✅ integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes |
| 5 | Preserve physical GPU embedding compatibility | Happy | Real model exports on both runtimes | Canaries, semantics and identities match | Integration | ❌ Physical native WebGPU qualification pending |
| 6 | Associate tensor batch outputs | Happy | Multiple distinct inputs | Each result matches its singleton source | Integration | ✅ integration/modules/inference/test_onnx_cpu.py::TestOnnxCpuProvider::test_preserves_text_batch_association |
| 7 | Complete a partial batch | Edge | One ready socket request | Completes without waiting for eight items | Contract | ✅ contract/runtime/compute/test_broker.py::TestBroker::test_isolates_bad_members_of_a_real_socket_batch |
| 8 | Preserve mixed-load interactive latency | Edge | Bulk import concurrent with search | p95 ratio ≤1.1 and end-to-end speedup ≥1.5 | E2E | ❌ Physical workload measurements pending |
| 9 | Complete aged backfill under GPU query load | Edge | Sustained interactive requests | Existing owner progresses without starvation | E2E | ❌ Physical mixed-load qualification pending |
| 10 | Bound resident allocations | Edge | Pinned and idle entries | Refuses over-budget admission; idle entries evicted | Unit | ✅ unit/runtime/compute/test_budget.py |
| 11 | Recover an allocation failure once | Error | Injected OOM on four inputs | Split once, preserve association, then CPU fallback on repeat failure | Unit | ✅ unit/runtime/compute/test_recovery.py::TestAllocationRecovery::test_splits_once_with_source_order |
| 12 | Recover broker ownership after death | Error | Kill actual CPU-mode broker | Replacement owner starts with private socket | E2E | ✅ e2e/test_compute_broker.py::TestComputeBroker::test_restarts_after_owner_death; unfinished GPU Job convergence still needs physical qualification |
| 13 | Refuse stale publication | Error | Source/lease changed | Late visual results cannot publish | Integration | ✅ integration/modules/search/test_visual_index.py::TestVisualIndex::test_rejects_late_visual_publication |
| 14 | Isolate a bad batch member | Error | Invalid member in real socket batch with injected execution | Healthy members keep correct results | Contract | ✅ contract/runtime/compute/test_broker.py::TestBroker::test_isolates_bad_members_of_a_real_socket_batch |
| 15 | Restrict diagnostics | Error | Anonymous/non-admin | 401/403 | Integration | ✅ integration/api/v1/test_system.py::TestComputeStatus |
| 16 | Execute both runtimes on intended hardware | Happy | Linux/Docker/WSL, NVIDIA/AMD/Intel | Physical adapter matched and real math passes | E2E | ❌ No supported hardware combination claimed |
| 17 | Reuse geometry uploads | Happy | Two views and repeated mesh, real optional runtime | One upload, repeatable output, mask/colour gates | Integration diagnostic | ✅ integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_reuses_uploaded_geometry_for_repeated_views; llvmpipe evidence only |
| 18 | Recover actual device OOM/loss | Error | Physical device fault | Bounded recovery and durable convergence | E2E | ❌ Injected tests are not hardware evidence |
| 19 | Preserve sparse batches | Happy | Fixed/dynamic graph, varied documents | Matches singleton terms and truncation | Integration | ✅ integration/modules/inference/test_sparse.py::TestSparseTensorBatch::test_matches_independent_documents |
| 20 | Preserve cancellation through fallback | Error | Withdrawn inference context | No CPU work starts | Unit | ✅ unit/runtime/compute/test_client.py::TestFallback::test_never_turns_cancellation_into_cpu_work |
| 21 | Bound queued frames | Edge | Aggregate input exceeds allowance | Rejects before accepting body bytes | Unit | ✅ unit/runtime/compute/test_budget.py::TestQueueBudget::test_admits_aggregate_frames_before_reading |
| 22 | Keep valid component beside invalid component | Error | Two components of one parsed source | Valid six-view result retained; independent error | Integration | ✅ integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestComponentBatch::test_keeps_valid_component_after_an_invalid_member |
| 23 | Restore units across eight-input chunks | Happy | Two six-view prepared units | Correct unit association over batches of eight and four | Unit | ✅ unit/modules/inference/test_batches.py::TestPreparedBatches::test_retains_unit_association_across_tensor_batches |
| 24 | Refuse insufficient performance evidence | Edge | Slow or interactive-regressing receipt | Receipt rejected | Unit | ✅ unit/runtime/compute/test_qualification.py::TestQualification::test_rejects_insufficient_performance |
| 25 | Keep canonical processing around native drawing | Happy | Prepared backend | Common alpha and vignette applied | Core | ✅ backend/packages/printstash-core/tests/mesh/rasterizer/test_prepared.py::TestPreparedRasterizer::test_keeps_common_frame_processing |

| 26 | Preserve rendering after inference startup failure | Error | Independent native ONNX startup error | Rendering device retained; inference unavailable | Unit | ✅ unit/runtime/compute/test_dispatcher.py::TestRuntimeIsolation::test_keeps_render_device_when_inference_runtime_fails |
| 27 | Admit larger warm batches | Edge | Larger workspace peak after model residency | Extra bytes reserved without reloading; oversized request refused | Unit | ✅ unit/runtime/compute/test_budget.py::TestResidency::test_grows_a_warm_model_without_reloading and test_keeps_warm_model_after_larger_batch_is_refused |
| 28 | Validate optional render completion | Error | Incomplete frames or withdrawn caller | Invalid output falls back; cancellation propagates | Unit | ✅ unit/modules/media/test_compute_render.py::TestOptionalRender |
| 29 | Preserve optional image deployment contracts | Happy | Built GPU variant | Startup, storage and backup/restore contracts pass | E2E image | ✅ tests/e2e/runtime_image.py; 10 passed |

| 30 | Preserve opacity along shared GPU edges | Error | Orthographic box at 224 pixels on physical RTX 5060 | Exact foreground mask and RGBA difference at most 8 | Integration diagnostic | ✅ integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_preserves_shared_edges_in_orthographic_views |

## Evidence recorded during implementation

- Focused inference/media/API and broker checks: 457 passed.
- Additional qualification, allocation, sparse and optional renderer checks: 58 passed.
- Real socket scheduler fault isolation and broker restart: 2 passed.
- Ingestion, indexer and component changes: 100 passed.
- Core prepared-renderer and rasterizer checks: 181 passed.
- Latest focused diagnostics, fallback, admission and receipt checks: 47 passed.
- Independent native startup and sparse canary checks: 25 passed; final memory/render/startup contracts: 12 passed.
- Optional GPU Docker image built successfully; image startup/storage contracts: 10 passed; a container probe exposing /dev/dxg and WSL driver libraries still enumerated only llvmpipe (no Dozen ICD).
- Required PR CI (including the complete core gate) passed at commit 11ae80a8. Results for the final commit are recorded in the pull request.
- The broad local full run was interrupted after 46 minutes: 20,281 passed, 29 failed and 38 errors. It ran while source/recipes and fixtures were being revised, producing parent/child embedding-space mismatches and stale OpenAPI/test-path failures. This is not a successful full gate. Stable-tree rechecks are reported in the pull request.
- An initial broad local run stopped after missing full-extra dependencies (879 passed, one failure and two collection errors); installing the full extra preceded the full-suite run. This was not a successful gate.

The explicit optional diagnostic fails if runtime/device prerequisites are absent; it is invoked separately from ordinary CPU CI. The development machine exposed llvmpipe to wgpu. Its synthetic box/sphere results and injected faults do not authorize receipts or NVIDIA/AMD/Intel support claims. The outstanding rows above block physical acceleration qualification, while normal CPU deployment remains available.

Follow-up [physical RTX 5060 investigation](compute-rtx5060-investigation.md): both native Windows canaries pass; experimental WSL rendering diagnostics pass after a shared-edge correction. Dozen still blocks ONNX WebGPU and 2/84 expanded render views fail the quality gate, so no production qualification is claimed.

See [compute deployment and qualification](../compute.md) and ADRs [0015](../adr/0015-gpu-rendering.md) / [0016](../adr/0016-native-worker-startup.md).

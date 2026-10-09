# Render-only Docker/WSL evaluation

Visual acceptance belongs to the operator. Preview mode supplies candidates and
does not fabricate a quality/performance qualification receipt. Automatic routing
still requires the original quality, end-to-end speed and mixed-load latency gates.

## Scope and physical evidence (2026-10-10)

The private operator corpus contains 92 files: 46 STL, 39 3MF and seven STEP.
The original archive was preserved; evaluation used copies in separate vaults.
The complete GPU-enabled ASGI ingestion run published 92 requested thumbnails and
metadata outputs, with every original verified by content hash. Placement was
65 GPU renders, 25 preserved embedded images and two CPU paths (large geometry /
streaming). It exercised durable Jobs, isolated parsers, encoding and publication.
It excludes HTTP socket latency. A separate CPU run completed all 92 inputs;
published metadata matches in every pair and all originals pass content verification.
Twenty-seven encoded thumbnails are byte-identical (embedded images and CPU paths).
The other comparisons are available in the private local review gallery. File names
and images remain private.

Hardware: RTX 5060 through `/dev/dxg`, wgpu's GL backend, Mesa D3D12
25.0.7-2+deb13u1; DXCore vendor 4318, device 11525, driver 9007199255790656.
All application and rendering processes ran inside Docker/WSL. Hardware identity
and a compute canary were checked; no Windows helper or vendor compute API was used.
The ONNX runtime was not initialized. See [deployment](../compute.md).

The optional real-device diagnostic passed eight cases. These cover toy geometry
pixel gates, upload reuse, resident geometry, readback grouping, cache eviction and
the large-singleton→small-batch regression. They do not approve the operator's corpus.

## Prepared-geometry experiment

Forty STL inputs were parsed in disposable CPU subprocesses, then sent through real
broker IPC. The client bounded outstanding transfer staging to 192 MiB. These are
render-only observations under other active tests, **not ingestion speedup evidence**.
Per-item timings exclude preparation and PNG file writes; pass wall times include
those PNG writes. They exclude durable ingestion/publication and interactive search.

Broker launch/status took 5.23 s; the internal adapter/canary timer was 3.28 s. Shader creation remains in the first render pass.

| Pass | Client concurrency | Outputs | Wall time | New readback groups | New submitted frames | New geometry upload bytes |
|---|---|---|---|---|---|---|
| 1 | 1 | 40/40 | 25.20 s | 39 | 39 | 130716096 |
| 2 | 4 | 40/40 | 8.33 s | 21 | 39 | 0 |
| 3 | 4 | 40/40 | 7.88 s | 23 | 39 | 0 |

One duplicate shares geometry: 39 unique GPU buffers remained resident across all
three passes. Total accounted device residency ended at 319,766,836 bytes within
the configured 1 GiB ceiling. The RGBA host cache is bounded at 32 MiB, so a set
larger than that does not falsely promise every shaded image remains cached.

An earlier unbounded transfer experiment received capacity refusals. That exposed
an IPC classification issue: the owner now acknowledges staging admission before
the client uploads the body, preserving capacity as a normal CPU-fallback reason.
The synthetic staging-refusal contract and physical successful run are separate
evidence. No physical device-loss or hardware-OOM claim is made.

## Software validation

The focused compute/media/real-IPC suite passes 73 cases. Three additional isolated-worker cases verify that only visual work starts the GPU owner; the metadata-only case failed before its fix. Core mesh tests pass
642 cases. Ruff, scoped formatting and backend pyright pass. The `gpu-render`
image passes all ten startup/storage/backup contracts; a fresh vault on that final
image also publishes three corpus inputs (two GPU renders and one embedded image).
The local gallery's 92 cards, 184 images, filters and review export passed a browser
functional check. The full backend lane and latest CI status belong to the PR
validation record rather than these physical measurement claims.

## Coverage matrix

Test paths below are relative to `backend/tests/`, except the explicit core path.
Physical diagnostics are opt-in and are not silently counted as ordinary CI tests.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Verified hardware identity | Happy | Unknown GL plus matching DXCore hardware record | Hardware IDs and driver retained | Unit | ✅ `unit/runtime/compute/test_dxcore.py::test_recognizes_verified_hardware` |
| 2 | Unproven hardware refused | Edge | Missing, mismatched or ambiguous evidence | Unknown adapter remains unqualified | Unit | ✅ `unit/runtime/compute/test_dxcore.py::test_refuses_unproven_hardware` |
| 3 | Software remains ineligible | Error | Software renderer names | No GPU classification | Unit | ✅ `unit/runtime/compute/test_dxcore.py::test_does_not_promote_software` |
| 4 | Native classification preserved | Edge | CPU adapter classification | Not promoted by DXCore | Unit | ✅ `unit/runtime/compute/test_dxcore.py::test_does_not_override_native_classification` |
| 5 | Missing host bridge falls back | Error | Unavailable DXCore library | No hardware evidence returned | Unit | ✅ `unit/runtime/compute/test_dxcore.py::test_requires_available_host_hardware_evidence` |
| 6 | Deployment backend selection | Happy | Native auto, WSL auto, explicit overrides | Expected backend selected without vendor branching | Unit | ✅ `unit/runtime/compute/test_discovery.py::test_selects_the_deployment_backend` |
| 7 | Supersampled memory admitted | Happy | Prepared thumbnail | Host/device requirements cover supersampling | Unit | ✅ `unit/runtime/compute/test_render_policy.py::test_accounts_for_supersampled_buffers` |
| 8 | False workload identity refused | Error | Wrong recipe or work units | Invalid request rejected | Unit | ✅ `unit/runtime/compute/test_render_policy.py::test_rejects_mislabelled_work` |
| 9 | Malformed geometry refused | Error | Invalid binary frame | Rejected before GPU work | Unit | ✅ `unit/runtime/compute/test_render_policy.py::test_rejects_malformed_geometry` |
| 10 | Batch workspace shared | Happy | Eight compatible frames | Lower allocation than eight independent workspaces | Unit | ✅ `unit/runtime/compute/test_render_policy.py::test_batches_share_workspace` |
| 11 | Oversized batch refused | Edge | Nine ready frames | Capacity refusal | Unit | ✅ `unit/runtime/compute/test_render_policy.py::test_refuses_more_than_eight_frames` |
| 12 | Rendering leaves ONNX dormant | Happy | Preview broker startup | No inference factory initialized | Unit | ✅ `unit/runtime/compute/test_dispatcher.py::test_preview_does_not_start_inference` |
| 13 | ONNX failure isolated | Error | Native inference startup fails | Render device survives | Unit | ✅ `unit/runtime/compute/test_dispatcher.py::test_keeps_render_device_when_inference_runtime_fails` |
| 14 | Preview is rendering-specific | Edge | CPU/auto and preview/qualified combinations | Only explicit rendering preview bypasses receipts | Unit | ✅ `unit/runtime/compute/test_client.py::test_requires_explicit_render_evaluation` |
| 15 | Failed startup cools down | Error | Unavailable broker device | Subsequent rendering uses CPU during cooldown | Unit | ✅ `unit/runtime/compute/test_client.py::test_cools_failed_owner_startup` |
| 16 | CPU mode avoids startup | Happy | CPU-only deployment | No optional owner launched | Unit | ✅ `unit/runtime/compute/test_client.py::test_cpu_deployment_does_not_start_an_owner` |
| 17 | Singleton evidence cannot qualify a batch | Edge | Old singleton receipt | Batch admission refused | Unit | ✅ `unit/runtime/compute/test_qualification.py::test_refuses_batching_with_singleton_evidence` |
| 18 | Qualified batch size bounded | Edge | Measured batch ceiling | Only covered counts admitted | Unit | ✅ `unit/runtime/compute/test_qualification.py::test_accepts_only_the_qualified_batch_size` |
| 19 | Batch failure isolates its members | Error | Malformed member or combined capacity refusal | Healthy results retain original associations; singleton flushes promptly | Contract | ✅ `contract/runtime/compute/test_broker.py::test_preserves_render_members_after_batch_refusal` |
| 20 | Staging pressure preserves owner | Error | Request body exceeds queue admission | Capacity response, healthy next request, no cooldown | Contract | ✅ `contract/runtime/compute/test_broker.py::test_refuses_staging_pressure_without_poisoning_owner` |
| 21 | CPU deployment publishes thumbnails | Happy | CPU, auto or preview without optional runtime | Original plus requested durable outputs ready | E2E | ✅ `e2e/test_ingest.py::test_publishes_mesh_outputs_without_gpu_qualification` |
| 22 | Repeated cameras reuse upload | Happy | Box/sphere, repeated views | Same pixels; one upload/readback; cache hit | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_reuses_uploaded_geometry_for_repeated_views` |
| 23 | Shared edges preserved | Happy | Orthographic box views | Exact alpha≥128 mask; max RGBA difference≤8 | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_preserves_shared_edges_in_orthographic_views` |
| 24 | GPU workspace bounded | Edge | Insufficient workspace budget | Refusal before allocation | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_refuses_workspace_growth_over_budget` |
| 25 | Distinct models share completion fence | Happy | Box and sphere ready together | Two associated outputs, one readback, resident geometry | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_batches_distinct_models_under_one_readback_fence` |
| 26 | Camera batch ceiling enforced | Edge | Ten cameras | Capacity refusal before submission | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_rejects_oversized_camera_batch` |
| 27 | Shaded cache bounded | Edge | Injected 20 KiB cache ceiling | Idle output evicted within ceiling | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_evicts_shaded_outputs_at_the_host_cache_ceiling` |
| 28 | Warm buffers support changing workload | Edge | Large singleton then small-model batch | Same renderer; grouped completion stays within budget | Integration (physical GPU) | ✅ `integration/modules/media/webgpu_render_diagnostic.py::test_batches_small_models_after_a_large_singleton` |
| 29 | Canonical postprocessing retains transparency | Happy | GPU-format readback pixels | Dimensions and alpha preserved | Core unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::test_preserves_transparency_in_gpu_readback` |
| 30 | Private corpus completes | Happy | 92 STL/3MF/STEP files, actual Docker/WSL GPU | 92 durable outputs; original bytes verified | Manual E2E | ✅ Local corpus report; aggregate evidence below |
| 31 | Forty models reuse persistent ownership | Happy | 40 prepared STL inputs, three passes | 120 GPU results; 39 unique buffers uploaded once | Manual integration | ✅ Local prepared-batch report; scope below |
| 32 | Operator accepts visual quality | Happy | Local CPU/GPU gallery | Human decision recorded separately | Manual E2E | ⏭️ N/A — operator review pending |
| 33 | Automatic performance qualification | Happy | Controlled accepted-Artifact → publication and mixed search | ≥1.5× and interactive p95≤1.1 | E2E | ⏭️ N/A — no receipt issued; shared-load runs do not qualify |
| 34 | Other hardware combinations qualified | Happy | Representative AMD/Intel/native Linux drivers | Physical deployment evidence for each combination | E2E | ⏭️ N/A — unavailable hardware; no support claim |
| 35 | Full-image transports preserved | Happy | Built gpu-render image | S3/WebDAV/SFTP/Drive available | E2E image | ✅ `e2e/runtime_image.py::TestFullImageTransports::test_constructs_advertised_image_transports` |
| 36 | S3 restore preserved | Happy | Backup from shipped gpu-render image | Backup restores successfully | E2E image | ✅ `e2e/runtime_image.py::TestRuntimeImageBackup::test_restores_s3_backup_from_shipped_image` |
| 37 | SFTP restore preserved | Happy | Backup from shipped gpu-render image | Backup restores successfully | E2E image | ✅ `e2e/runtime_image.py::TestRuntimeImageBackup::test_restores_sftp_backup_from_shipped_image` |
| 38 | CPU-only metadata avoids GPU cold start | Edge | Metadata-only, thumbnail and visual-analysis requests | Owner starts only for visual work | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestComputeStartupPlacement::test_warms_render_owner_only_for_visual_work` |

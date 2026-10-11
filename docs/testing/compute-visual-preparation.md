# Compact GPU visual preparation

This change moves visual welding, angle-weighted normals, bounds and framing to
portable WebGPU passes. It does not change authoritative geometric measurements.
Integer atomics give bounded visual normal accumulation without vendor extensions.
Degenerate faces, excessive local degree and hash congestion fall back locally to
flat normals; malformed descriptors and invalid source indices remain errors.

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Transfer compact geometry | Happy | Canonical, deferred, visual packets | Same identity and source arrays; no copied staging body | Unit | Pass |
| Refuse invalid staging | Error | Truncated header/body, incompatible camera, invalid counts | Rejected before GPU exchange | Unit | Pass |
| Validate owner arrays | Error | NaN, excessive coordinates, invalid face indices | Owner refuses input | Unit | Pass |
| Weld visual duplicates | Happy | Separate indices with identical positions | Angle-weighted smooth normals agree within visual tolerance | GPU diagnostic | Pass |
| Isolate degenerate/high-degree vertices | Edge | Valid body with degenerate or overfull neighborhood | Valid faces survive, no whole-model retry | GPU diagnostic | Pass |
| Keep batching bounded | Edge | Multiple prepared models under a device budget | All buffers charged, one bounded submission/readback | Integration | Pass |
| Complete cached mixed-format ingestion | Happy | At least200 identical cached STL/OBJ/STEP sources | Durable requested outputs, timings and batch counts | Deployment | Pending |

Visual acceptance belongs to the user; images need not be pixel-identical to the
CPU renderer. Renderer-only timing does not establish ingestion throughput. GPU
measurements will use the same selected sources and retain the historical CPU
baseline without rerunning CPU ingestion. No new R2 requests are permitted.

## Executed gates

- 209 focused media/compute tests passed, covering immutable geometry, lazy CPU
  recovery, cache/admission boundaries and batch association.
- 52 compact-upload, ownership, bounds, status/API and OpenAPI tests passed after
  switching to mapped upload slabs. The OpenAPI change adds four diagnostic
  counters; ingestion and derivative responses do not change.
- 28 explicit GPU diagnostic cases passed in Docker/WSL on the RTX 5060 using the
  image's Mesa 25.0.7 D3D12/OpenGL path and wgpu 0.32. This includes real welding,
  overfull/cancelling/degenerate neighborhoods, grouped readbacks, caches,
  postprocessing and exceptional resource cleanup. Software test doubles cover
  injected allocation/copy failures separately; these are not physical OOM tests.
- Backend Pyright passed with 0 errors. The shared encoder/profile/visual-recipe
  selection passed 224 core tests and 83 backend tests.

The matched deployment benchmark uses 200 cached sources (84 STL, 83 OBJ, 33 STEP).
The baseline completed 200 original verifications and 200 thumbnails, with every
requested metadata/thumbnail derivative ready and no new R2 requests. Candidate
source-to-publication measurement is pending; no speedup or visual acceptance is
claimed by the shader diagnostic results.

The WSL monotonic and UTC durations diverged during the baseline. Windows
Stopwatch/QPC observations bracket the benchmark independently; wrapper durations
and all clock domains are retained. No clock was changed or timing corrected by
an inferred factor. Device counters and reserved-byte snapshots are not physical
VRAM peaks or GPU-busy percentages.

## Wire and visual-normal behaviors

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Owned staging retains array identity | Happy | Three geometry representations | Same content identity, read-only decoded arrays and zero-copy wire body | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry_wire.py::TestSplitThumbnail::test_roundtrips_owned_staging_without_array_copy` |
| Malformed staging is refused before exchange | Error | Truncated header/body, invalid camera/count/mode | No packet proceeds to GPU exchange | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry_wire.py::TestSplitThumbnail::test_refuses_malformed_staging_before_exchange` |
| Owner validates staged arrays | Error | Nonfinite/out-of-range position or invalid face index | Device decoder refuses input independently of wire framing | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry_wire.py::TestSplitThumbnail::test_owner_still_validates_each_array_after_staging` |
| Real shader canary has bounded ownership | Happy | Duplicate positions and degenerate face | Expected visual normals and released temporary allocations | Physical integration | ✅ `backend/tests/integration/modules/media/gpu_visual_normals_diagnostic.py::TestVisualNormalPipeline::test_executes_startup_canary_with_bounded_ownership` |
| Unhealthy neighborhoods degrade locally | Edge | Duplicate, overfull, cancelling or degenerate neighborhoods | Valid triangles survive; unhealthy local normals flatten without retrying the model | Physical integration | ✅ `backend/tests/integration/modules/media/gpu_visual_normals_diagnostic.py::TestVisualNormalPipeline::test_limits_unhealthy_neighborhoods_to_local_flat_normals` |
| Eligible visual work skips CPU normal preparation | Happy | Available GPU and at least 20,000 faces | No CPU welding or normal computation before GPU placement | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestDeferredPreparation::test_selects_gpu_before_cpu_normals_at_profitable_threshold` |
| Small/unavailable work preserves CPU preparation | Edge | Small model or unavailable device | Canonical prepared result | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestDeferredPreparation::test_keeps_unprofitable_preparation_canonical` |
| CPU recovery reuses prepared ownership | Error | Deferred or visual GPU representation needs CPU fallback twice | Same lazy canonical snapshot is reused | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestDeferredPreparation::test_preserves_parser_fallback_custody` |

## Counter compatibility

The old `render_frames` counter counted draws per camera, not every native queue
submission. Compare completed model/frame histograms across versions. Both
histograms include completed attempts later retried after normal preparation;
cache hits do not add a rendered batch. The new `render_submissions` counts one
image submission per batch and is a separate metric.

`geometry_upload_bytes` previously excluded per-frame configuration; it now
includes compact geometry plus 256 bytes per frame. `render_upload_calls` is new
and counts explicit mapped slabs (plus legacy CSR writes), not every hidden driver
operation. `geometry_gpu_uploads` now describes compact uploads at all model
sizes, rather than selecting the former packing path. Old and new packing timings
cover different scopes and must not be compared as an equivalent stage timer.
In both versions, `rendering_transfer_seconds` includes readback synchronization;
it is not pure transfer time and is not additive to broker execution time.

## Compact renderer: behaviour-to-test evidence

These rows document assertions already executed in the mapped-upload candidate gates; no additional tests were run while preparing this fragment. Unit tests use the native API double in [tests/fakes/gpu_device.py](../../backend/tests/fakes/gpu_device.py), which records commands, copies, mappings and ownership but does not execute WGSL. Physical rows exercised the Docker/WSL adapter. All rows passed in the reported 52-test focused gate or 28-case physical gate, as applicable.

| Behaviour | Category | Input | Observable assertion | Exact test | Tier |
| --- | --- | --- | --- | --- | --- |
| Group uploads and retain staging until completion | Happy | Two canonical models and configs | Zero queue writes, one mapped upload; staging survives until the fence, geometry remains resident, final ledger returns to zero | [`TestCompactGeometry::test_retains_grouped_uploads_until_completion`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L39) | Unit |
| Split slabs at native limits | Boundary | max-buffer-size=64 | Six bounded slabs preserve positions/config bytes and share one submit | [`TestCompactGeometry::test_grouped_upload_splits_at_native_buffer_limit`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L63) | Unit |
| Omit CPU visual identities and normals | Happy | One-triangle VisualRender | 36 position + 12 index + 256 config bytes uploaded; one upload call, telemetry status, ledger equals declared allocation | [`TestCompactGeometry::test_visual_upload_has_no_cpu_identities_or_normals`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L83) | Unit |
| Clean partial allocations | Error | Injected third allocation failure | Close fences once, destroys all allocated buffers and returns ledger to zero | [`TestCompactGeometry::test_retires_partial_allocations_after_completion`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L108) | Unit; not physical OOM |
| Share commands across models/cameras | Happy | Two models with 2+1 views | Three native triangle draws; distinct 256-byte camera/readback slots; one upload, submit and map; model bin2/frame bin3 | [`TestNativeBatch::test_coalesces_multiple_models_into_one_native_batch`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L122) | Unit |
| Keep full-vertex framing off CPU | Regression | Array raises on matmul/min/max | Rendering succeeds, source bytes unchanged, bounds count equals input vertices | [`TestNativeBatch::test_render_does_not_rotate_or_reduce_full_vertices_on_cpu`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L153) | Unit |
| Reuse geometry and bounds for another camera | Happy | Same geometry, different output key | One geometry upload/bounds pass, two submissions; only another config is uploaded | [`TestNativeBatch::test_reuses_prepared_geometry_for_another_camera`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L187) | Unit |
| Release custody after native-operation failure | Error | Submit/map/mapped-write failures | Unverified geometry, pending buffers and staging retire after attempted completion; ordinary pins released | [`TestNativeBatch::test_failure_retires_unverified_geometry_after_completion`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L200) | Unit; 3 injected cases |
| Serve output cache without GPU work | Happy | Identical request repeated | Equal output; upload, submit and map counters stay at one | [`TestNativeBatch::test_repeated_output_does_not_upload_submit_or_map`](../../backend/tests/unit/modules/media/test_gpu_compact.py#L226) | Unit |
| Charge reduction intermediates | Boundary | 3/64/65/4096/4097 vertices | Scratch sizes cross workgroup and second-level boundaries correctly | [`TestViewPreparation::test_bounds_reduction_charges_every_intermediate_level`](../../backend/tests/unit/modules/media/test_gpu_views.py#L21) | Unit; 5 cases |
| Align automatic/explicit camera slots | Happy | Automatic camera and explicit identity/matte | 256-byte configs, correct flags/style; workspace is eight config plus eight frame slots | [`TestViewPreparation::test_aligns_camera_configuration_slots`](../../backend/tests/unit/modules/media/test_gpu_views.py#L24) | Unit |
| Bound reduction dispatch dimensions | Boundary | 640 vertices; workgroup dimension limit4 | Dispatches (4,3,1), then (1,1,1) | [`TestViewPreparation::test_large_reduction_uses_bounded_two_dimensional_dispatch`](../../backend/tests/unit/modules/media/test_gpu_views.py#L35) | Unit |
| Discard unverified deferred upload | Lifetime | Upload then close without rendering | Geometry remains pinned while pending; close empties geometry/unverified state and ledger | [`TestDeferredGeometry::test_renderer_discards_unverified_geometry_on_close`](../../backend/tests/unit/modules/media/test_gpu_geometry.py#L445) | Updated unit |
| Keep deferred buffers compact | Regression | Two-face deferred input | 36/24/12/36/32-byte buffers, pending CSR plan, no premature CPU normal fallback | [`TestDeferredGeometry::test_renderer_keeps_deferred_geometry_compact`](../../backend/tests/unit/modules/media/test_gpu_geometry.py#L460) | Updated unit |
| Use compact layout for small inputs | Boundary | Two faces, legacy threshold argument3 | 140-byte resident allocation, GPU compact counter1, CPU expanded-packing counter0 | [`TestRendererUpload::test_small_models_share_the_compact_path`](../../backend/tests/unit/modules/media/test_gpu_geometry.py#L480) | Updated unit |
| Reuse the same compact allocation | Happy | Repeated geometry key | Same object, one upload, one cache hit, one pending item | [`TestRendererUpload::test_reuses_the_same_compact_allocation`](../../backend/tests/unit/modules/media/test_gpu_geometry.py#L496) | Updated unit |
| Reopen after workspace eviction | Lifetime | Upload, close, upload again | First fence closes; new owner/fence opens; cleanup returns ledger to zero | [`TestRendererUpload::test_reopens_owner_after_renderer_workspace_eviction`](../../backend/tests/unit/modules/media/test_gpu_geometry.py#L510) | Updated unit |
| Separate frame/submission/upload telemetry | Observability | Two models producing three views | 3 frames, 1 batch/submission/upload, 6 bounds vertices, null legacy threshold, zero normal fallbacks | [`TestRenderTelemetry::test_counts_frames_separately_from_native_submissions`](../../backend/tests/unit/runtime/compute/test_dispatcher.py#L95) | Unit |
| Expose CPU-mode diagnostic defaults | API | Authenticated CPU-mode request | Four additive counters are zero; legacy threshold null | [`TestComputeStatus::test_reports_cpu_override`](../../backend/tests/integration/api/v1/test_system.py#L59) | API integration |
| Preserve real extrema and repeated views | Numerical | Box/sphere, two repeated views | Repeated/cache outputs agree, foreground/transparency exist, GPU bounds exactly match canonical f32 extrema | [`TestWebGpuRender::test_reuses_uploaded_geometry_for_repeated_views`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L47) | Physical GPU; 2 cases |
| Avoid shared-edge cracks | Edge | Six orthographic box views | Substantial foreground; every occupied scanline is contiguous | [`TestWebGpuRender::test_preserves_shared_edges_in_orthographic_views`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L94) | Physical GPU |
| Refuse excessive attachment workspace | Error | Workspace limit1 | Capacity refusal before creating color texture | [`TestWebGpuRender::test_refuses_workspace_growth_over_budget`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L118) | Physical GPU diagnostic |
| Preserve distinct models under one fence | Happy | Box/sphere batch then singles | Distinct outputs, batch/single equality, uploads reused, one initial submission/readback | [`TestWebGpuRender::test_batches_distinct_models_under_one_readback_fence`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L127) | Physical GPU |
| Keep deprecated threshold argument compatible | Compatibility | Same sphere; threshold None and1 | Both use current compact rendering; outputs agree within assertions; pending state cleared | [`TestWebGpuRender::test_packs_indexed_models_on_gpu`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L150) | Physical GPU; not old CPU-vs-GPU packing |
| Fence uploaded-but-unrendered resources | Lifetime | Upload, close before render, reopen | Pending resources retire; first fence closed, new owner live, final ledger zero | [`TestWebGpuRender::test_reuses_renderer_after_unrendered_packing`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L180) | Physical GPU |
| Retain healthy models during legacy normal recovery | Regression | Healthy box and two opposed-face geometries | Only refused models retry; healthy result retained; repeated request uses verified cache | [`TestWebGpuRender::test_preserves_healthy_members_during_normal_recovery`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L207) | Physical GPU |
| Retain legacy numerical guards | Numerical | Sphere/thin box/subnormal facet with visible box | Fallbacks0/0/1; same GPU raster with canonical versus deferred normals agrees within existing assertions | [`TestWebGpuRender::test_preserves_rendered_pixels_with_deferred_normals`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L255) | Physical GPU; 3 cases, not CPU-raster equality |
| Prevent unverified normal cache reuse | Lifetime | Deferred upload, close, later render | Unverified geometry removed; complete later render verifies new geometry | [`TestWebGpuRender::test_discards_unverified_normal_geometry_before_reuse`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L302) | Physical GPU |
| Keep visual degeneracy local | Edge | Duplicate-index box soup with/without degenerate face | One submit/readback, two GPU normal preparations, positive local telemetry, zero CPU model retries; original bytes intact; later singles agree | [`TestWebGpuRender::test_visual_normals_repair_degenerates_without_retrying_models`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L330) | Physical GPU |
| Reuse renderer after larger singleton | Admission | Large singleton then two smaller models | Same renderer, two additional uploads, one additional grouped submit/readback, ledger within capacity | [`TestWarmPoolAdmission::test_batches_small_models_after_a_large_singleton`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L401) | Physical GPU in independent process; injected discovery/host admission, not E2E |
| Preserve postprocessing at supersampling boundary | Numerical | 64x48,640x480,641x33 | GPU-finalized render equals a separate same-recipe render finalized on CPU | [`TestGpuPostprocess::test_preserves_canonical_mesh_pixels`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L510) | Physical GPU; 3 cases, not old/new raster equality |
| Read only active retained-buffer span | Boundary | Eight-view batch then one view | Reads one active frame rather than retained eight-frame capacity | [`TestGpuPostprocess::test_limits_readback_after_large_batch`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L555) | Physical GPU |
| Recover from finalizer canary refusal | Error | Injected GPU postprocess refusal | Supersampled raw response remains valid; CPU postprocessing recovers expected output | [`TestGpuPostprocess::test_falls_back_after_postprocess_canary_refusal`](../../backend/tests/integration/modules/media/webgpu_render_diagnostic.py#L574) | Physical GPU with injected canary refusal |

The 52-test gate includes additional tests in its selected files; the 28-case physical gate also includes the separate visual-normal provider diagnostics. Neither total counts rows in this matrix. Exact-image checks above compare repeated new-recipe execution or its postprocessing/legacy-normal variants. They do not reinstate CPU-raster pixel equality as a requirement. User visual acceptance and matched source-to-publication throughput remain separate evidence.


## Immutable readback replies

This follow-up removes redundant host copies after the existing GPU completion
fence. The mapped range is still copied into owned immutable bytes before unmap;
each returned/cache packet is a separate bytes object. The contiguous branch
joins its header with only that input's frame span. Padded rows still strip native
alignment, and CPU postprocessing retains its existing input and output contract.
Neither the output recipe nor the host-cache/admission budgets change.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | preserves frame association | Happy | Two models with 2+1 views; contiguous raw, padded raw, GPU-finalized cases | Exact per-model header/pixels in view order; no row padding; immutable bytes and exact cache-byte accounting | Unit | ✅ [`TestExecutePreparedMany::test_preserves_frame_association`](../../backend/tests/unit/modules/media/test_webgpu_render.py#L60), 3 cases passed |
| 2 | owns cached pixels after unmap | Edge | Native mapped storage overwritten during unmap, then identical request | First reply and cached reply retain original bytes; no second submission/readback | Unit | ✅ [`TestExecutePreparedMany::test_owns_cached_pixels_after_unmap`](../../backend/tests/unit/modules/media/test_webgpu_render.py#L107) |
| 3 | excludes normal status from returned pixels | Edge | Two visual inputs, three frames, two nonzero local-normal counters after pixels | Exact frame packets exclude status trailer; counters remain telemetry with no whole-model CPU retry | Unit | ✅ [`TestExecutePreparedMany::test_excludes_normal_status_from_returned_pixels`](../../backend/tests/unit/modules/media/test_webgpu_render.py#L139) |
| 4 | preserves CPU postprocessing of padded readback | Edge | Supersampled partial-alpha pixels with native row padding | Final packet equals existing CPU postprocessing of the unpadded source pixels | Unit | ✅ [`TestExecutePreparedMany::test_preserves_cpu_postprocessing_of_padded_readback`](../../backend/tests/unit/modules/media/test_webgpu_render.py#L182) |

The six new cases passed within the 50-test focused renderer/bounds/lifecycle/
telemetry gate, including the exact single-behaviour test-name hygiene check.


The physical follow-up first reported 27 passes and one strict comparison failure
at 640x480. The exact case passed alone, and a full rerun passed all 28 cases; a
subsequent full repetition reproduced the failure at 64x48. Private captures
showed a nonempty first raster followed by entirely transparent rasters. CPU and
GPU postprocessing of the same later raw raster agreed exactly. Positions,
indices, identities, normals, bounds, camera configs and generated frame buffers
retained identical bytes across the failure. Private experiments changing the
frame binding, viewport/scissor, depth comparison, attachment reuse and canary
ordering did not resolve it; none of those renderer variants were retained.

The diagnostic's warm-pool case created and destroyed a second GPU device while
the session's first device remained live. Excluding that case yielded three
successive 27-case passes (81 executions). Reintroducing it reproduced the
transparent-raster failure (27 passed, one failed). The warm-pool case now runs
its actual dispatcher/admission exercise and all existing assertions in a fresh
Python process with a 60-second timeout. This matches the broker's single-device
process ownership and preserves coverage; the case is not skipped. This is
observed diagnostic cross-device contamination on the tested runtime, not a
claim about the underlying driver mechanism or universal raster determinism.

After process isolation, three complete physical diagnostic runs against the
repository files passed unchanged RGBA assertions: 28/28 in 18.03 seconds,
28/28 in 17.87 seconds and 28/28 in 17.72 seconds (84 executions total).
These are correctness diagnostics, not performance measurements. Private failure
captures and the reintroduced-device red run remain under
`/tmp/printstash-rgba-investigation`; final complete-run logs are
`/tmp/printstash-rgba-isolated-full-{1,2,3}.log` on the test host.


## Immutable geometry IPC

The geometry encoder now joins the validated, normalized array buffers directly
into one immutable packet. Splitting that packet returns a readonly memoryview
whose owner is the original bytes object; decoded arrays retain that owner after
the caller releases its view. This removes intermediate full-body copies without
changing the JSON descriptor, array dtypes, packet bytes, geometry identities,
admission ceiling or render recipe. Mutable packet backing is rejected, including
a readonly view of a bytearray. Transport framing, cancellation and cache ownership
remain covered by the existing client and broker contract tests.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | preserves exact wire bytes | Regression | Canonical, deferred and visual geometry; strided big-endian source arrays; automatic and explicit cameras | Packet equals an independent literal-JSON and struct-packed oracle; immutable bytes and exact size | Unit | ✅ [`TestEncode::test_preserves_exact_wire_bytes`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L218), 3 cases passed |
| 2 | snapshots mutable source arrays | Lifetime | Each geometry mode encoded before source arrays are overwritten | Packet and decoded vertices/faces retain their original values | Unit | ✅ [`TestEncode::test_snapshots_mutable_source_arrays`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L245), 3 cases passed |
| 3 | rejects nonfinite camera JSON | Error | Camera contains NaN | Encoding rejects the JSON value before returning a packet | Unit | ✅ [`TestEncode::test_rejects_nonfinite_camera_json`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L262) |
| 4 | retains geometry admission boundary | Boundary | Each geometry mode with its canonical-equivalent envelope at or one byte over the configured limit | Exact-limit geometry encodes/decodes; over-limit geometry is refused | Unit | ✅ [`TestEncode::test_retains_geometry_admission_boundary`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L274), 6 cases passed |
| 5 | returns readonly view of packet owner | Ownership | Each encoded geometry mode | Body shares the packet owner, is contiguous and readonly, and preserves header/body identity | Unit | ✅ [`TestSplit::test_returns_readonly_view_of_packet_owner`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L290), 3 cases passed |
| 6 | keeps decoded cache alive after releasing body view | Lifetime | Packet variable deleted and caller view explicitly released | Cached decoded arrays remain valid and readonly | Unit | ✅ [`TestSplit::test_keeps_decoded_cache_alive_after_releasing_body_view`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L311), 3 cases passed |
| 7 | rejects mutable packet backing | Error | Bytearray or its readonly memoryview | Split refuses backing that can still change outside the view | Unit | ✅ [`TestSplit::test_rejects_mutable_packet_backing`](../../backend/tests/unit/modules/media/test_compute_geometry.py#L339), 2 cases passed |

All 21 new cases passed within a 143-test focused gate covering geometry codec and
wire validation, render requests, batch/cache ownership, client transport and real
socket broker contracts. No GPU workload or performance measurement was run for
this copy-only follow-up; the removed copies do not establish an end-to-end speedup.

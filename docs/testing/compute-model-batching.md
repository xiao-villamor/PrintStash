# Ready-model batching and bounded preparation

This follow-up corrects the priority and preparation paths that prevented ready
models from reaching the existing render coalescer together. It preserves the
10 ms maximum ready-work wait, eight-frame ceiling, interactive priority,
disposable parsing, resource ceilings and per-Artifact publication authority.
It also records the earlier 1000-model baseline without treating renderer timing
as complete-pipeline timing. The new comparison of the same 1000 cached models
is pending; visual acceptance remains the operator's decision.

## Diagnosis and scope

`WorkPriority` is a ContextVar whose default is `INTERACTIVE`. A fresh worker
launched through exec did not inherit its parent's context, so backfill rendering
could arrive at the broker as interactive work. Such tickets dispatch immediately
and cannot use the intentional background coalescing window. The earlier run's
967 singleton frames establish the absence of observed cross-model batches;
its counters did not record individual request priorities. The priority loss is
established by the worker code path, not by an inferred priority histogram.

The supervisor now exports the scheduling priority and the bootstrap restores
its scope before executing the worker module. The mesh worker separately scopes
asynchronous thumbnail/derivative rendering as background compute work, even when
the owning Job has interactive priority. Job/native admission keeps its original
priority. Direct interactive rendering remains interactive, backfill never raises
compute priority, and exception exit restores the caller's scope. This separation
allows existing admitted derivative work to coalesce without delaying direct
interactive compute requests.

The broker packs already-admitted geometry by total camera frames and available
device/readback budget, rather than treating each request as one frame. Three
four-view requests therefore use at most two requests in the first batch. Excess
requests remain ready for subsequent execution; coalescing does not start source
parsers or extend the original 10 ms window. Invalid/unprepared members do not
justify optimistic batch admission. Legacy requests still undergo the final
dispatcher admission checks.

Tickets are revalidated individually before scheduling and again before
execution. Expiry or cancellation retires only that member. Shared execution's
watchdog allows the remaining live deadlines, while each result remains subject
to its own cancellation, deadline and publication fences. The scheduler releases
retired decoded buffers before signalling a handler that can return its byte
credits. Real-socket tests cover a live member surviving another member's expiry
or cancellation and subsequent work using the same healthy owner.

Administrator diagnostics add `render_interactive_requests` and
`render_background_requests` for admitted render requests, plus
`render_batch_model_counts` and `render_batch_frame_counts`. Each histogram has
eight exact bins, for one through eight model inputs or rendered frames per
completed readback batch. Model inputs count requests with uncached output, not
deduplicated geometry keys. These are not cumulative buckets. A fully cached
output creates no readback and increments neither histogram. A multi-view model
can increment the one-model bin and the two-frame bin in the same batch.

## Bounded preparation and dispatch

Native admission previously treated ASCII STL and OBJ complexity as unknown and
reserved the whole pool. An optional text preflight now validates a source within
8 MiB, 4096-byte lines and 0.1 seconds before using the existing weighted claim.
ASCII STL reuses the canonical facet scanner; OBJ counts triangulated faces and
validates coordinate/index bounds without creating native geometry arrays.
Stable source identity is checked across the scan. Unsupported or uncertain
syntax, material references, source changes and exceeded bounds retain the
whole-pool reservation. Mixed requests remain conservative if any source is
unknown. Analysis/raster allowances, per-face coefficients and hard worker
ceilings remain in force; this does not introduce persistent source parsers.

Three CPU preparation changes avoid redundant work. A freshly loaded scene with
one exactly identity-placed geometry transfers its owned buffers directly;
transforms and repeated placements retain canonical flattening. Sequential
independent STL facets skip an initial watertightness check whose answer is
already known, then still copy/weld and apply the canonical topology and volume
checks. Smooth-normal preparation reuses computed lengths while retaining its
zero-length guards. Regression cases cover source immutability, canonical OBJ
geometry/measurements/thumbnail bytes, open and inverted facet refusals, and
zero or cancelling normals.

The DBOS queue polling interval defaults to 0.25 seconds through
`VAULT_JOBS_QUEUE_POLL_INTERVAL_SECONDS` (allowed range 0.05–5 seconds). An explicit
engine-constructor interval still takes precedence. This changes the dispatch
cadence; recovery scanning has its own cadence. Optional format and fingerprint
imports are deferred until used. The simple-mesh child no longer imports unused
inference owners; Trimesh and its numerical imports still belong to the fresh
child's load phase. No persistent parser or preloaded forkserver is introduced.

## Historical 1000-model baseline

These are aggregate observations from the earlier CPU/GPU comparison. They are
not results of the corrections above. Each arm processed the same 1000-model
workload. The GPU arm used the physical RTX 5060 through Docker/WSL.

| Observation | GPU arm | CPU arm | Interpretation |
|---|---:|---:|---|
| Whole-pipeline elapsed time | 3222.049 s | 4050.997 s | Measured wall time for each arm |
| Render completion batches / frames | 967 / 967 | Not applicable | Every observed GPU batch was a singleton |
| Native admissions | 1000 | 1000 | Admission count, not concurrent workers |
| Whole-pool native claims | 641 at 3955 MiB | 641 at 3955 MiB | Conservative claims that restrict simultaneous preparation |
| Sum of native admission waits | 3832.265 s | 4342.811 s | Overlapping per-request waits; not wall time |
| Aggregate supervision time | 2958.542 s | 3640.361 s | Worker-supervision measurement, not GPU execution time |
| Broker GPU execution | 45.805 s | Not applicable | Owner execution counter; excludes the complete pipeline |
| Broker cold start | 1.926 s | Not applicable | Separate owner startup observation |

Admission waits can overlap between requests and must not be added to, or
substituted for, whole-pipeline elapsed time. Supervision also has a different
scope from broker execution. The 45.805-second GPU execution counter cannot
explain or stand in for the 3222.049-second pipeline result. These observations
motivate improving preparation and ready-work delivery as well as batching;
they do not establish the benefit of any individual correction.

The next comparison must reuse the same 1000 cached inputs without external
source fetches and retain complete publication outcomes, physical adapter
identity, batch model/frame histograms, admitted priorities, preparation/admission
observations and separate CPU/GPU elapsed times. No new 1000-model result,
performance qualification or operator visual acceptance is recorded here.

## Behaviour coverage

The first matrix contains 53 rows covering 19 broker/renderer behaviours, 18 bounded-preflight behaviours,
eight CPU preparation behaviours, one worker-priority behaviour with two cases,
four compute-priority behaviours, one mesh-worker dispatch behaviour with two
cases and two queue-configuration behaviours. Parametrized cases stay in their
single behaviour row. Three existing GPU tests gain histogram assertions.

Identifiers are relative to the repository root. A checkmark means the named
regression coverage exists; it is not an end-to-end performance or visual
qualification. The ordinary backend gate and explicit GPU diagnostic are
separate runs; software adapters accepted by diagnostics do not establish
physical-device qualification. Final full-gate results belong with the change's
validation record. The new 1000-model comparison remains pending.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `test_accepts_eight_frames` | Happy | Two admitted four-view requests | Eight frames fit the available device budget | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_accepts_eight_frames` |
| 2 | `test_leaves_excess_frames_for_another_batch` | Edge | Three admitted four-view requests | Twelve frames are refused as one batch | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_leaves_excess_frames_for_another_batch` |
| 3 | `test_respects_device_capacity` | Edge | Eight frames with a one-byte device allowance | Batch is refused before execution | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_respects_device_capacity` |
| 4 | `test_leaves_invalid_identity_for_individual_execution` | Error | One member has inconsistent work units | Invalid member is not combined with a healthy request | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_leaves_invalid_identity_for_individual_execution` |
| 5 | `test_requires_independent_geometry_admission` | Edge | One binary member has no admitted decoded geometry | Coalescing refuses the unprepared member | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_requires_independent_geometry_admission` |
| 6 | `test_preserves_legacy_request_admission` | Happy | Legacy render requests | Final dispatcher admission remains available | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_preserves_legacy_request_admission` |
| 7 | `test_preserves_live_ticket_order` | Happy | Two live tickets | Original order and unsignalled completion events are preserved | Unit | ✅ `backend/tests/unit/runtime/compute/test_broker.py::TestActiveTickets::test_preserves_live_ticket_order` |
| 8 | `test_retires_only_expired_members` | Error | One expired and one live ticket | Only the expired member receives a deadline result | Unit | ✅ `backend/tests/unit/runtime/compute/test_broker.py::TestActiveTickets::test_retires_only_expired_members` |
| 9 | `test_retires_only_cancelled_members` | Error | One cancelled and one live ticket | Only the cancelled member receives cancellation | Unit | ✅ `backend/tests/unit/runtime/compute/test_broker.py::TestActiveTickets::test_retires_only_cancelled_members` |
| 10 | `test_retires_a_fully_cancelled_group` | Edge | All members cancelled | No executable member remains | Unit | ✅ `backend/tests/unit/runtime/compute/test_broker.py::TestActiveTickets::test_retires_a_fully_cancelled_group` |
| 11 | `test_releases_retired_geometry_before_waking_handler` | Edge | Cancelled binary ticket owns decoded arrays | Arrays are reclaimable when its completion event is signalled | Unit | ✅ `backend/tests/unit/runtime/compute/test_broker.py::TestActiveTickets::test_releases_retired_geometry_before_waking_handler` |
| 12 | `test_packs_multiview_requests_within_eight_frames` | Happy | Three concurrent four-view requests through real sockets | Two requests share a batch; the third follows; all results and later work succeed | Contract | ✅ `backend/tests/contract/runtime/compute/test_broker.py::TestRenderLifecycle::test_packs_multiview_requests_within_eight_frames` |
| 13 | `test_preserves_live_members_after_a_deadline` | Error | Short and live deadlines share active execution | Short member gets deadline; live member and subsequent request succeed | Contract | ✅ `backend/tests/contract/runtime/compute/test_broker.py::TestRenderLifecycle::test_preserves_live_members_after_a_deadline` |
| 14 | `test_prioritizes_interactive_ready_work` | Happy | Interactive and background tickets ready behind a blocker | Interactive dispatch precedes background dispatch; results retain association | Contract | ✅ `backend/tests/contract/runtime/compute/test_broker.py::TestRenderLifecycle::test_prioritizes_interactive_ready_work` |
| 15 | `test_preserves_live_members_after_cancellation` | Error | One client cancels during shared execution | Cancellation is isolated; live member and subsequent request succeed | Contract | ✅ `backend/tests/contract/runtime/compute/test_broker.py::TestRenderLifecycle::test_preserves_live_members_after_cancellation` |
| 16 | `test_reports_admitted_render_priorities` | Happy | Two admitted tickets of each priority | Diagnostics count two interactive and two background requests | Contract | ✅ `backend/tests/contract/runtime/compute/test_broker.py::TestRenderLifecycle::test_reports_admitted_render_priorities` |
| 17 | `test_batches_distinct_models_under_one_readback_fence` | Happy | Two different prepared models, then separate executions | First readback records two models/two frames; later singleton bins increment independently | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_batches_distinct_models_under_one_readback_fence` |
| 18 | `test_reuses_uploaded_geometry_for_repeated_views` | Happy | Box and sphere cases; one geometry with two views | One geometry upload; histogram records one model/two frames | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_reuses_uploaded_geometry_for_repeated_views` |
| 19 | `test_reuses_final_shaded_outputs` | Happy | Same finalized output requested twice | Identical output, no new submission and no new model-histogram entry | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestGpuPostprocess::test_reuses_final_shaded_outputs` |
| 20 | `test_validated_text_source_uses_a_weighted_claim` | Happy | Small validated ASCII STL or OBJ; 4 GiB pool | One slot and 512 MiB claim | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_validated_text_source_uses_a_weighted_claim` |
| 21 | `test_bounds_obj_coordinate_buffers_before_weighting` | Happy | Valid normal/UV/all/relative OBJ indices | Each source receives the bounded 512 MiB claim | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_bounds_obj_coordinate_buffers_before_weighting` |
| 22 | `test_unverified_obj_indices_retain_full_pool` | Error | Malformed, missing or out-of-range OBJ index channels | Whole-pool reservation retained | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_unverified_obj_indices_retain_full_pool` |
| 23 | `test_embedded_material_reference_retains_whole_pool` | Edge | Material-library reference inside an OBJ comment | Whole-pool reservation retained | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_embedded_material_reference_retains_whole_pool` |
| 24 | `test_text_estimation_preserves_analysis_work` | Happy | Small ASCII STL with analysis work | Analysis floor remains 1 GiB | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_text_estimation_preserves_analysis_work` |
| 25 | `test_unverified_text_retains_whole_pool` | Error | Incomplete/nonfinite text, invalid indices, materials, continuations, excess coordinates or non-ASCII | Every uncertain source retains the whole-pool reservation | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_unverified_text_retains_whole_pool` |
| 26 | `test_oversized_text_remains_unknown_without_loading` | Edge | Sparse ASCII STL/OBJ source exceeds the 8 MiB preflight limit | Whole-pool reservation; sparse source remains unmaterialized | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_oversized_text_remains_unknown_without_loading` |
| 27 | `test_long_text_line_retains_whole_pool` | Edge | Text line exceeds the 4096-byte preflight limit | Whole-pool reservation retained | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_long_text_line_retains_whole_pool` |
| 28 | `test_expired_text_preflight_retains_whole_pool` | Edge | Preflight deadline already expired | Whole-pool reservation retained for either text format | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_expired_text_preflight_retains_whole_pool` |
| 29 | `test_changed_source_retains_whole_pool` | Error | Source is replaced during identity checks | Replacement cannot authorize a smaller claim | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_changed_source_retains_whole_pool` |
| 30 | `test_combines_validated_text_with_binary_complexity` | Happy | 200000 binary STL triangles plus two ASCII facets | Existing per-face coefficient charges all 200002 triangles | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_combines_validated_text_with_binary_complexity` |
| 31 | `test_unknown_source_keeps_a_mixed_request_conservative` | Edge | Known ASCII STL plus unknown 3MF source | Mixed request retains the whole pool | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestEstimateSources::test_unknown_source_keeps_a_mixed_request_conservative` |
| 32 | `test_counts_only_faces_in_obj_with_polylines` | Happy | OBJ includes point/polyline records and one face | Count equals the real mesh loader: one triangle | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestObjTriangleCount::test_counts_only_faces_in_obj_with_polylines` |
| 33 | `test_counts_triangulated_faces` | Happy | Triangle, relative indices, quad and n-gon | Counts are respectively one, one, two and three triangles | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestObjTriangleCount::test_counts_triangulated_faces` |
| 34 | `test_text_source_renders_under_its_weighted_native_ceiling` | Happy | ASCII STL and OBJ in a real guarded CPU child | 512 MiB admitted claim yields a thumbnail and one-triangle geometry | Integration | ✅ `backend/tests/integration/modules/media/test_native_budget.py::TestAdmittedGeometry::test_text_source_renders_under_its_weighted_native_ceiling` |
| 35 | `test_validates_complete_source_facets` | Happy | Two complete ASCII STL facets | Canonical bounded scanner returns two | Integration | ✅ `backend/tests/integration/modules/media/test_stl_reader.py::TestCountAsciiTriangles::test_validates_complete_source_facets` |
| 36 | `test_preserves_missing_endsolid_compatibility` | Edge | Complete facets without the optional terminal marker | Existing compatibility returns two | Integration | ✅ `backend/tests/integration/modules/media/test_stl_reader.py::TestCountAsciiTriangles::test_preserves_missing_endsolid_compatibility` |
| 37 | `test_refuses_incomplete_bounded_count` | Error | Triangle, source-byte or deadline limit reached | STLBudgetExceeded reports the corresponding limit | Integration | ✅ `backend/tests/integration/modules/media/test_stl_reader.py::TestCountAsciiTriangles::test_refuses_incomplete_bounded_count` |
| 38 | `test_transfers_single_identity_geometry_buffers` | Happy | Fresh scene with one exactly identity-placed geometry | Result shares owned vertices/faces and preserves source/name/node metadata | Unit | ✅ `backend/tests/unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_transfers_single_identity_geometry_buffers` |
| 39 | `test_preserves_a_transformed_single_geometry` | Edge | One geometry translated by a scene transform | Returned vertices include translation; source arrays remain unchanged | Unit | ✅ `backend/tests/unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_preserves_a_transformed_single_geometry` |
| 40 | `test_preserves_repeated_geometry_placements` | Edge | Two placements reference the same geometry | Both placements appear in bounds/faces; original geometry is unchanged | Unit | ✅ `backend/tests/unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_preserves_repeated_geometry_placements` |
| 41 | `test_measures_independent_facets_without_changing_source` | Happy | Sequential and reordered independent box facets | Exact dimensions, triangle count and volume; source arrays unchanged | Unit | ✅ `backend/tests/unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_measures_independent_facets_without_changing_source` |
| 42 | `test_retains_open_independent_facet_volume_refusal` | Error | Independent facets with one face missing | Volume remains unavailable as not watertight | Unit | ✅ `backend/tests/unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_retains_open_independent_facet_volume_refusal` |
| 43 | `test_retains_inverted_independent_facet_volume_refusal` | Error | Independent facets with inverted winding | Volume remains unavailable for non-positive integral | Unit | ✅ `backend/tests/unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_retains_inverted_independent_facet_volume_refusal` |
| 44 | `test_preserves_canonical_obj_outputs` | Happy | Real exported OBJ compared with canonical scene flattening | Identical vertices/faces, measurements and thumbnail bytes | Integration | ✅ `backend/tests/integration/modules/media/mesh_loading/test_obj_outcomes.py::TestLoadMesh::test_preserves_canonical_obj_outputs` |
| 45 | `test_preserves_normalization_for_zero_lengths` | Edge | Plane, degenerate face and opposed faces | Expected normal vectors, including zero vectors, remain exact | Unit (core) | ✅ `backend/packages/printstash-core/tests/mesh/test_render_geometry.py::TestPrepareMeshRender::test_preserves_normalization_for_zero_lengths` |
| 46 | `test_preserves_scheduling_priority_in_the_disposable_child` | Happy | Interactive and backfill scopes cross real guarded exec | Child reports the original scheduling priority in both cases | Integration | ✅ `backend/tests/integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_preserves_scheduling_priority_in_the_disposable_child` |
| 47 | `test_direct_render_preserves_interactive_dispatch` | Happy | Direct rendering without derivative scope | Compute priority remains interactive | Unit | ✅ `backend/tests/unit/modules/media/test_compute_render.py::TestRenderPriority::test_direct_render_preserves_interactive_dispatch` |
| 48 | `test_derivative_render_allows_background_coalescing` | Happy | Rendering inside the derivative scope | Compute priority is background | Unit | ✅ `backend/tests/unit/modules/media/test_compute_render.py::TestRenderPriority::test_derivative_render_allows_background_coalescing` |
| 49 | `test_background_scope_restores_the_callers_priority` | Error | Exception exits the derivative scope | Caller returns to interactive compute priority | Unit | ✅ `backend/tests/unit/modules/media/test_compute_render.py::TestRenderPriority::test_background_scope_restores_the_callers_priority` |
| 50 | `test_backfill_never_raises_compute_priority` | Happy | Backfill scheduling priority without derivative scope | Compute priority remains background | Unit | ✅ `backend/tests/unit/modules/media/test_compute_render.py::TestRenderPriority::test_backfill_never_raises_compute_priority` |
| 51 | `test_derivative_frames_use_batchable_compute_dispatch` | Happy | Real mesh-worker flow with interactive/backfill Job scopes; GPU egress seam | Valid thumbnail frame and background binary render request in both cases | Integration | ✅ `backend/tests/integration/modules/media/test_mesh_worker.py::TestMain::test_derivative_frames_use_batchable_compute_dispatch` |
| 52 | `test_dispatch_cadence_uses_the_deployment_setting` | Happy | Deployment polling setting is 0.25 seconds | Registered derivative queue uses 0.25 seconds | Unit | ✅ `backend/tests/unit/runtime/engine/test_dbos_engine.py::TestQueues::test_dispatch_cadence_uses_the_deployment_setting` |
| 53 | `test_explicit_cadence_overrides_the_deployment_default` | Edge | Engine constructor explicitly requests 0.05 seconds | Registered ingestion queue uses the explicit value | Unit | ✅ `backend/tests/unit/runtime/engine/test_dbos_engine.py::TestQueues::test_explicit_cadence_overrides_the_deployment_default` |

## GPU geometry packing candidate

The indexed WGSL packer expands indices and blends crease normals directly in
VRAM for inputs with at least 20,000 faces. Smaller inputs retain CPU packing;
this threshold does not waive the outer render qualification policy. The native
API fake below checks resource ownership, not shader execution.

The threshold follows 12 physical-RTX-5060 cases with five alternating warm
repetitions each. Both arms use the same GPU rasterizer: the comparison isolates
CPU versus GPU geometry packing, from an already prepared model through final
640×480 RGBA. It includes upload and completion, but excludes source parsing,
normal preparation, IPC, encoding and publication. Batch4 uses four distinct
uniform-scale variants. Every foreground mask matched and maximum RGBA
component difference was 1. Those observations are diagnostic evidence; operator
visual acceptance remains separate.

| Faces | Singleton median ratio | Four-model median ratio |
| ---: | ---: | ---: |
| 80 | 0.746× | 0.912× |
| 320 | 0.803× | 0.792× |
| 1,004 | 0.850× | 0.878× |
| 1,280 | 0.909× | 0.876× |
| 5,000 | 1.127× | 1.119× |
| 5,120 | 1.213× | 1.172× |
| 20,300 | 1.605× | 1.847× |
| 20,480 | 2.234× | 2.140× |
| 80,920 | 2.404× | 2.239× |
| 81,920 | 4.296× | 4.570× |
| 296,538 | 2.247× | 2.257× |
| 327,680 | 6.514× | 6.636× |

Discovery took 2.875 s separately. The first GPU packing operation took 172.6 ms,
including setup, versus 61.1 ms for CPU packing in that cold control; warm ratios
must not be substituted for cold-start cost. Contexts, shaders and buffers remain
resident for later admitted models.

A separate guarded-normal prototype measured five cached sources, five
interleaved repetitions per path, including welding, CSR construction, buffer
allocation/upload, compute, the final status fence and cleanup. Its medians below
exclude parsing, centering, IPC and image rendering. They are not ingestion ratios.

| Faces | CPU normals + CPU packing | CPU normals + GPU packing | GPU normals + GPU packing |
| ---: | ---: | ---: | ---: |
| 474 | 2.60 ms | 3.59 ms | 2.98 ms |
| 602 | 2.96 ms | 3.41 ms | 3.52 ms |
| 1,864 | 5.27 ms | 5.25 ms | 3.94 ms |
| 6,026 | 8.00 ms | 6.52 ms | 4.02 ms |
| 77,630 | 131.57 ms | 91.38 ms | 60.60 ms |

The largest candidate ranged from 59.00 to 61.78 ms. No corpus fallback occurred
in these 25 timed prototype executions; that sample does not estimate a complete
corpus fallback rate. Opposing-face cancellation canaries exercised CPU fallback.
The final integrated normal path passed seven physical diagnostic cases, and the
combined focused recheck passed 130 tests. CI for the correction commit and the
complete cached-corpus comparison remain pending. The production guard is stricter than this prototype, so its fallback
frequency must be measured again on the final corpus run.


Native API fakes validate buffer ownership and fallback; only the explicit physical diagnostics execute shader work on actual hardware. GPU normal attempts and CPU retries are counted separately.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Charges compact buffers | Happy | Validated render arrays | Exact indexed buffer byte counts | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestInputSizes::test_charges_exact_compact_buffers` |
| 2 | Preserves CPU triangle output | Happy | Plane plus degenerate facet | Expected positions, normals and zero padding | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestPackedChunks::test_preserves_flat_face_output` |
| 3 | Invalidates a released fence | Edge | Ledger releases the persistent fence | Destroyed buffer, closed handle and idempotent close | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestCompletionFence::test_ledger_release_invalidates_the_native_buffer` |
| 4 | Uploads indexed geometry | Happy | Prepared model | Compact payloads and 108 transferred bytes | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_uploads_indexed_buffers_without_triangle_expansion` |
| 5 | Pins pending GPU inputs | Edge | Eviction pressure before completion | Capacity refusal; active buffers remain alive | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_protects_pending_inputs_from_eviction` |
| 6 | Releases after existing fence | Happy | Caller readback completed | Inputs destroyed, persistent 4 B fence retained, no second wait | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_releases_inputs_after_existing_fence` |
| 7 | Uses preallocated exceptional fence | Error | Pending work without completed frame readback | One clear/map completion; no new buffer allocation; idempotent release | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_waits_once_on_exceptional_cleanup` |
| 8 | Rejects excess device budget | Error | 108 input bytes + 4 B fence / 100 B budget | Capacity error before input allocation | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_refuses_excess_capacity_before_allocating` |
| 9 | Rejects storage binding overflow | Error | 16 B storage binding ceiling | Capacity error before input allocation | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_refuses_device_storage_limit_before_allocating` |
| 10 | Releases partial allocations | Error | Injected native buffer allocation failure | Inputs destroyed after fence; zero ledger after close | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_releases_partial_allocations_after_failure` |
| 11 | Retains failed submission inputs | Error | Injected native submit failure | Inputs pinned until fenced cleanup | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_retains_inputs_after_failed_submission` |
| 12 | Rejects incorrect canary output | Error | Native fake returns all-zero output | Canary refusal; no leaked GPU leases after close | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestGeometryPacker::test_refuses_canary_output_mismatch` |
| 13 | Falls back after canary refusal | Error | Native fake fails canary | Canonical CPU output, counters and disabled packing candidate | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestRendererUpload::test_uses_cpu_after_packing_canary_refusal` |
| 14 | Reopens after workspace eviction | Edge | Renderer closes, then uploads new model | Old fence destroyed; fresh packer; zero ledger after cleanup | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestRendererUpload::test_reopens_packer_after_renderer_workspace_eviction` |
| 15 | Skips packing startup for small models | Edge | Below crossover face count | Canonical CPU packing; no packer, fence or startup canary | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestRendererUpload::test_small_models_skip_gpu_packing_setup` |
| 16 | Packs indexed models on physical GPU | Happy | Actual RTX 5060, sphere and startup canary of 504 deterministic faces | Same alpha, RGBA difference <=8 versus CPU packing with same GPU rasterizer; no pending inputs | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_packs_indexed_models_on_gpu` |
| 17 | Fences without a frame and reopens | Error | Actual GPU packing queued; close before a frame readback | Preallocated native clear/map fence completes, fresh packer on reuse, no residual leases | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_reuses_renderer_after_unrendered_packing` |
| 18 | Charges deferred normal allocations | Happy | Deferred render arrays | All shared inputs, CSR, smooth output and status sum to expected bytes | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestInputSizes::test_charges_deferred_normal_allocations` |
| 19 | Shares GPU preparation inputs | Happy | Deferred geometry and native API fake | One positions/faces/IDs upload shared by normals and packing; scratch pinned until completion | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestDeferredGeometry::test_shares_gpu_preparation_inputs` |
| 20 | Uses CPU normals after failed canary | Error | Native API fake returns incorrect normal canary | Canonical normals uploaded; no GPU-normal status retained | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestDeferredGeometry::test_uses_cpu_normals_when_startup_canary_refuses` |
| 21 | Falls back above vertex valence limit | Edge | More than 4096 face corners at one position | Canonical normals; no GPU normal attempt or leaked lease | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestDeferredGeometry::test_falls_back_for_excessive_vertex_valence` |
| 22 | Falls back outside GPU numeric range | Edge | Finite coordinates outside safe f32 norm envelope | Canonical per-item fallback; no GPU normal allocation | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestDeferredGeometry::test_falls_back_for_gpu_unsafe_coordinate_range` |
| 23 | Drops unverified geometry on close | Error | Native API fake has pending normal status | Geometry removed after fence; no retained leases | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestDeferredGeometry::test_renderer_discards_unverified_geometry_on_close` |
| 24 | Materializes normals for CPU packing | Happy | Deferred input with GPU packing disabled | Canonical packed bytes; CPU fallback counter; no packer startup | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_geometry.py::TestDeferredGeometry::test_renderer_materializes_cpu_normals_without_gpu_packing` |
| 25 | Preserves healthy batch members during numerical recovery | Error | Actual GPU: box plus two opposed-face geometries | Only refused geometries retry sequentially; healthy result preserved; all outputs associated and cached | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_preserves_healthy_members_during_normal_recovery` |
| 26 | Preserves rendered pixels after deferred normals | Edge | Actual GPU: sphere, sliver and visible box with subnormal facet | Alpha exact and max RGBA difference <=8; tiny facet triggers one canonical retry; repeated result cached | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_preserves_rendered_pixels_with_deferred_normals` |
| 27 | Discards unverified normals before physical reuse | Error | Actual GPU: upload then close before status readback | No stale geometry/leases; fresh render validates new GPU normal buffers | Integration (explicit GPU diagnostic) | ✅ `backend/tests/integration/modules/media/webgpu_render_diagnostic.py::TestWebGpuRender::test_discards_unverified_normal_geometry_before_reuse` |

Startup canary covers repeated/permuted weld IDs, inclined crease-threshold normals, nonzero repeated positions and distinct collinear/opposed faces. Comparing f32 cross products before normalization prevents the observed FMA residue without changing canonical CPU geometry. The guarded normal shader separately
refuses numerically unsafe faces and retries only the affected geometry on CPU.

Physical evidence: final integrated run passed 7 cases in 11.40 s; 22 native-boundary unit tests passed in 5.09 s. Scoped Ruff and Pyright passed; repository hygiene passed 5,194 checks in 481.61 s. Actual adapter is D3D12 (NVIDIA GeForce RTX 5060), OpenGL via Mesa 25.2.8 in WSL. Native API fakes are distinct from actual shader execution. The host queue.on_submitted_work_done_sync binding failed with a CFFI callback-signature TypeError; production instead uses a 4 B preallocated MAP_READ/COPY_DST completion fence with an explicit pinned ledger reservation. Frame completion reuses its existing map fence. GPU normals stay resident and feed the packer; status 4 B/model is appended to the existing frame readback, with no intermediate normal readback.

Numerically refused models retry sequentially per geometry so the maximum-scratch host reservation remains valid. GPU normal metric counts attempts; the CPU fallback metric includes eligibility/canary and final-status refusals. Valid canonical geometry, exact measurements and untrusted disposable parsing stay unchanged.

## Deferred normal preparation coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Canonical welding reuse (`test_reuses_canonical_welding_without_requantizing`) | Happy | Owned source geometry with previously computed read-only welded identities | Returned identities and smooth normals exactly match canonical preparation without requantizing | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_render_geometry.py::TestDeferredNormalHelpers::test_reuses_canonical_welding_without_requantizing` |
| 2 | Shared identity validation (`test_rejects_invalid_shared_identities`) | Error | Identities with wrong length, negative value, out-of-range value, or floating dtype | Raises invalid_render_welding | Unit | ✅ `backend/packages/printstash-core/tests/mesh/test_render_geometry.py::TestDeferredNormalHelpers::test_rejects_invalid_shared_identities` |
| 3 | Canonical fallback source custody (`test_preserves_canonical_fallback`) | Happy | Deferred snapshot followed by mutation of original source arrays | Fallback normals match the original snapshot; output retains owned read-only geometry | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestCanonical::test_preserves_canonical_fallback` |
| 4 | Broker cache remains bounded (`test_broker_fallback_does_not_grow_cached_descriptor`) | Edge | Canonical fallback requested twice without cache opt-in | Separate canonical results; deferred descriptor retains no cached canonical result | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestCanonical::test_broker_fallback_does_not_grow_cached_descriptor` |
| 5 | Explicit parser memoization (`test_parser_memoization_requires_explicit_opt_in`) | Edge | Canonical fallback requested with cache=True, then cache=False | Opted-in result is reused; uncached request produces a distinct result | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestCanonical::test_parser_memoization_requires_explicit_opt_in` |
| 6 | Stable CSR corner order (`test_preserves_original_corner_order_with_duplicate_positions`) | Happy | Mesh with duplicated positions and opposed faces | Each welded row contains original face-corner indices in stable order; CSR arrays are read-only | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestBuildCSR::test_preserves_original_corner_order_with_duplicate_positions` |
| 7 | Finite GPU coordinate range (`test_rejects_coordinates_outside_finite_gpu_arithmetic`) | Error | Coordinates scaled beyond the finite shader arithmetic bound | Raises compute_normals_numeric_range | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestBuildCSR::test_rejects_coordinates_outside_finite_gpu_arithmetic` |
| 8 | Nonzero subnormal CPU reference (`test_tiny_face_preserves_nonzero_canonical_normal`) | Edge | Orthogonal triangle with edges of approximately 1e-20 | Canonical smooth normals remain exactly [0, 0, 1] at each vertex | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestBuildCSR::test_tiny_face_preserves_nonzero_canonical_normal` |
| 9 | Bounded CSR valence (`test_refuses_unbounded_valence_before_sort`) | Error | One welded row exceeds MAX_DEGREE | Raises compute_normals_valence before attempting adjacency sorting | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestBuildCSR::test_refuses_unbounded_valence_before_sort` |
| 10 | Face chunk validation (`test_rejects_invalid_or_inconsistent_chunks`) | Error | Negative/out-of-range face index or emitted face count inconsistent with descriptor | Raises a compute_normals validation error | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestBuildCSR::test_rejects_invalid_or_inconsistent_chunks` |
| 11 | Complete normal device reservation (`test_accounts_for_all_normal_allocations`) | Happy | Valid CSR encoded with a recording device | Allocation byte total matches device_input_bytes; status/smooth buffers have required sizes | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestNormalPipeline::test_accounts_for_all_normal_allocations` |
| 12 | Partial allocation custody (`test_partial_failures_keep_every_allocation_with_callers_fence`) | Error | Third buffer allocation fails, or upload fails after buffers exist | Every created buffer remains with caller ownership and is not destroyed before its fence | Unit | ✅ `backend/tests/unit/modules/media/test_gpu_normals.py::TestNormalPipeline::test_partial_failures_keep_every_allocation_with_callers_fence` |
| 13 | Immutable deferred wire ownership (`test_preserves_deferred_wire_ownership`) | Happy | Encode/decode a deferred triangle | Tagged descriptor retains zero-copy read-only uint32 indices/identities and exact canonical fallback | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_preserves_deferred_wire_ownership` |
| 14 | Distinct normal representations (`test_distinguishes_normal_representations`) | Edge | Identical counts/body interpreted under canonical versus deferred layout | Layouts compare unequal and content identities differ | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_distinguishes_normal_representations` |
| 15 | Explicit normal tag validation (`test_rejects_unknown_explicit_normal_tags`) | Error | Explicit tag is null, canonical, unsupported version, or integer | Raises compute_geometry_normal_mode | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_rejects_unknown_explicit_normal_tags` |
| 16 | Required compact-layout tag (`test_missing_tag_cannot_reinterpret_compact_arrays`) | Error | Deferred compact arrays with normal_mode removed | Raises compute_geometry_size instead of treating compact arrays as canonical | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_missing_tag_cannot_reinterpret_compact_arrays` |
| 17 | Original admission ceiling (`test_retains_old_equivalent_admission_ceiling`) | Edge | Deferred header fits compact representation but exceeds canonical-equivalent 48 MiB limit | Raises compute_geometry_budget before decoding body arrays | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_retains_old_equivalent_admission_ceiling` |
| 18 | Compact array validation (`test_rejects_invalid_compact_arrays`) | Error | Invalid face/identity, NaN position, or inconsistent declared welded-position count | Raises compute_geometry_invalid | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_rejects_invalid_compact_arrays` |
| 19 | Mutable backing rejection (`test_rejects_mutable_backing_even_through_readonly_view`) | Error | Read-only memoryview backed by mutable bytearray | Raises compute_geometry_mutable | Unit | ✅ `backend/tests/unit/modules/media/test_compute_geometry.py::TestDeferredCodec::test_rejects_mutable_backing_even_through_readonly_view` |
| 20 | GPU selection before CPU normals (`test_selects_gpu_before_cpu_normals_at_profitable_threshold`) | Happy | Available render accelerator and geometry at the 20,000-face threshold | Returns DeferredRender without computing canonical CPU normals | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestDeferredPreparation::test_selects_gpu_before_cpu_normals_at_profitable_threshold` |
| 21 | Canonical preparation outside GPU eligibility (`test_keeps_unprofitable_preparation_canonical`) | Edge | 19,999 faces with accelerator available, or 20,000 faces with accelerator unavailable | Returns canonical PreparedRender | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestDeferredPreparation::test_keeps_unprofitable_preparation_canonical` |
| 22 | Parser fallback custody (`test_preserves_parser_fallback_custody`) | Edge | Two CPU-only render requests reuse one deferred parser snapshot | Both renders receive the same canonical snapshot with expected smooth normals | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestDeferredPreparation::test_preserves_parser_fallback_custody` |
| 23 | Canonical accelerated image encoding (`test_preserves_accelerated_encoding_policy`) | Happy | Accelerator returns RGBA pixels including transparent RGB; PNG and WEBP requested | Encoded bytes equal canonical encoder output and decoded RGBA is exact | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestAcceleratedEncoding::test_preserves_accelerated_encoding_policy` |
| 24 | Parent numerical import boundary (`test_module_import_keeps_numpy_out_of_parent`) | Edge | Fresh Python process imports the render facade | Process succeeds with numpy absent from sys.modules | Integration | ✅ `backend/tests/integration/modules/media/test_gpu_normals.py::TestRenderImport::test_module_import_keeps_numpy_out_of_parent` |

The five cached-corpus prototype cases established feasibility, but the final production numerical guard is stricter, so prototype timings do not establish final corpus performance or fallback frequency.
Focused verification passed 56 application tests and 32 core tests; scoped Pyright, Ruff checks, formatting checks, and the diff whitespace check also passed.

## Admission and publication coverage

The publication test has two parametrized cases (`canonical` and
`deferred-normals`). Its real worker, socket, scheduling and durable-publication
path uses an injected qualified renderer; shader correctness is established by
the separate physical diagnostics.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `test_reserves_gpu_packing_staging_once_per_geometry` | Edge | Two requests sharing one geometry at the device limit | Includes pinned staging exactly once; refuses insufficient budget | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py` |
| 2 | `test_small_cpu_packing_needs_no_gpu_staging` | Edge | Geometry below the packing crossover | Admits without unused GPU input buffers | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py` |
| 3 | `test_geometry_shader_changes_invalidate_qualification` | Error | Shader source changes | Runtime identity no longer matches previous qualification | Unit | ✅ `backend/tests/unit/runtime/compute/test_discovery.py` |
| 4 | `test_reports_cpu_override` | Happy | CPU-only policy | Geometry GPU counters remain zero and crossover absent | Integration | ✅ `backend/tests/integration/api/v1/test_system.py` |
| 5 | `test_release_callback_can_retire_owned_allocations` | Error | Eviction, expiry or pressure recovery closes an owner and its pinned children | Every allocation releases once without stale-key failures | Unit | ✅ `backend/tests/unit/runtime/compute/test_budget.py` |
| 6 | `test_dispatcher_shutdown_closes_renderer_before_its_pinned_children` | Error | Renderer has a persistent child and was recently reused | Renderer fences/releases children first; native device closes last | Unit | ✅ `backend/tests/unit/runtime/compute/test_budget.py` |
| 7 | `test_preserves_simple_mesh_outputs_without_optional_dependencies` | Happy | STL/OBJ child with unused inference/fingerprint/format modules blocked | Same geometry, volume, pixels and coverage; source unchanged | Integration | ✅ `backend/tests/integration/modules/media/test_mesh_isolation.py` |
| 8 | `test_host_admission_skips_allocations_retired_by_their_owner` | Error | RAM pressure closes renderer and its pinned child | Remaining idle allocation evicts without a stale-key error | Unit | ✅ `backend/tests/unit/runtime/compute/test_budget.py` |
| 9 | `test_bounds_deferred_normal_batch_resources` | Edge | Two deferred requests compared with canonical requests | Host admission includes one maximum normal scratch allocation plus 8 status bytes; device/readback each include 8 status bytes | Unit | ✅ `backend/tests/unit/runtime/compute/test_render_policy.py::TestAdmission::test_bounds_deferred_normal_batch_resources` |
| 10 | `test_reserves_deferred_normal_buffers_once_for_reused_geometry` | Edge | Two requests share deferred geometry; capacity is exact bound or one byte below | Exact bound admits both requests; one-byte shortage refuses; shared normal buffers charged once | Unit | ✅ `backend/tests/unit/runtime/compute/test_batching.py::TestRenderBatchFits::test_reserves_deferred_normal_buffers_once_for_reused_geometry` |
| 11 | `test_does_not_alias_different_normal_representations` | Error | Same cache key/counts requested under a different normal representation | Raises compute_geometry_reference without changing existing pin custody | Unit | ✅ `backend/tests/unit/runtime/compute/test_geometry_cache.py::TestGeometryCache::test_does_not_alias_different_normal_representations` |
| 12 | `test_refuses_views_from_before_gpu_normal_preparation` | Error | Thumbnail and multiview profiles use the previous v3 view recipe | Raises search_visual_recipe_invalid | Unit | ✅ `backend/packages/printstash-core/tests/search/test_visual_inputs.py::TestVisualRecipe::test_refuses_views_from_before_gpu_normal_preparation` |
| 13 | `test_publishes_the_healthy_member_when_a_batched_job_is_cancelled` | Error | Two parametrized cases: canonical geometry and deferred normals; two real mesh workers batch, then one Job is cancelled | Healthy Job publishes its associated thumbnail and metadata; cancelled member cannot publish after shared completion; original source bytes are preserved | E2E | ✅ `backend/tests/e2e/test_render_batch_publication.py::TestRenderBatchPublication::test_publishes_the_healthy_member_when_a_batched_job_is_cancelled` |

The added diagnostics and OpenAPI snapshot passed 21 focused cases; the staging
admission and broker group passed 29 cases. These are focused checks, not the
pending full-suite or the requested 1000-model performance result. Owned-resource
eviction regressions failed in all three initial cases, then 36 broker/packing
cases and 15 final budget/recovery cases passed. Optional-import changes passed
75 focused cases and two guarded-child fingerprint equivalence cases.

## Validation record

The final packing/normal integration passed 22 native-boundary unit tests and
seven physical GPU diagnostic cases. The normal-preparation change passed 56
application tests and 32 focused core tests. Repository hygiene passed 5,194
checks, and the complete core suite passed 677 tests; these scopes overlap and
must not be summed into an independent-case total. Scoped Ruff, formatting,
Pyright and diff whitespace checks passed.

The ordinary backend gate reported 20,696 passes, one stale in-process OpenAPI
snapshot failure and three WebDAV executable-discovery errors. All four passed
in a fresh run with the virtualenv executable directory on PATH. The final
focused gate reported 2,236 passes and ten failures: the versioned visual-recipe
fixture needed its identity updated, and nine storage cases shared a test root
with the concurrently running resource suite. That resource suite reported 685
passes and one PostgreSQL generation failure. A single subsequent sequential
run passed all 130 cases across the affected producers, geometry, normal and
batch-association files plus the exact PostgreSQL case. Storage, generation and
pixel assertions remained intact. The sequential recheck does not relabel the
original full runs as successful. Final Ruff and format checks passed; CI is
rerun after correcting CPU-only fake dependencies and the required test class.

A 12-model Docker/WSL smoke test completed all originals and requested outputs,
with exact geometry matching the previous evidence. It observed three two-input
readback batches, two GPU normal submissions and one isolated numerical CPU
retry. This was deployment validation while other tests were running, not a
performance measurement or visual approval.

The requested performance run uses the same 1,000 cached models (481 STL,
486 OBJ and 33 STEP), without further storage requests. Per operator instruction,
only the new GPU arm is rerun; the existing CPU and GPU arms remain references.
Their comparison measures the combined pipeline change, since CPU preparation
also changed. Final normal fallback frequency, complete-pipeline timing and
operator visual acceptance remain pending. Earlier focused checks and prototype
timings do not qualify those outcomes.

The contract probe uses real socket framing, scheduling and response handling
with an injected executor seam. It proves lifecycle/packing decisions, not GPU
speed or image quality. The explicit WebGPU diagnostic supplies the readback and
histogram assertions with a real runtime. The guarded text-source test executes
the real CPU child under its admitted memory ceiling. Existing source, generation
and attempt fences continue to control durable publication.

See [compute deployment and qualification](../compute.md),
[the rendering ADR](../adr/0015-gpu-rendering.md),
[the worker-lifecycle ADR](../adr/0016-native-worker-startup.md) and
[earlier render-transfer evidence](compute-render-performance.md).

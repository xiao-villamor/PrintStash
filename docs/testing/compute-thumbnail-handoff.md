# Combined thumbnail ownership and retired handoff evidence

This page preserves the retired child-finalizer experiment and the subsequent
combined-worker validation. The current implementation is the
[direct-codec handoff](compute-thumbnail-handoff-v2.md): it retains combined work
when ineligible and does not launch an encoder child on successful GPU output.
The historical results below do not measure that new implementation.

The `5814e6e7` selective experiment completed 200/200 sources with exact geometry
and zero R2 requests, but did not establish an end-to-end gain: 445.148 seconds
monotonic versus 447.574 baseline, below five-second observation resolution. Its
54 finalizers accumulated 76.483 seconds of supervision and 147.526 seconds of
readmission; their GPU dispatch accumulated 15.792 seconds. These sums overlap
and must not be subtracted from wall time. Whole-pool admission is a conservative
memory decision, not evidence of profitable process separation. The completed combined-worker measurements are recorded in the
[pipeline evidence](compute-native-pipeline-200.md).

The retained combined path preserves the compact GPU renderer, binary framing limits,
empty-frame CPU fallback, shared lossless encoder, generated-frame preservation,
cooperative cancellation, original bytes and authoritative geometry. The E2E
covers both a device refusal and an all-transparent response through the same
single worker and existing publication fences.

## Retained combined-path coverage

| Behaviour / test | Category | Precondition / input | Observable outcome | Tier | Status |
|---|---|---|---|---|---|
| `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_retains_one_worker_per_source` | lifecycle | Known STL or unknown-complexity 3MF | One guarded execution publishes metadata and thumbnail | integration | Verified |
| `backend/tests/e2e/test_render_batch_publication.py::TestThumbnailFallbackPublication::test_preserves_publication_across_gpu_outcomes` | fallback publication | Real ingest/Job/parser/broker; device failure or empty GPU response | One worker publishes CPU fallback; source and exact measurements retained; full native/prepared capacity recoverable | E2E | Verified |
| `backend/tests/e2e/test_render_batch_publication.py::TestRenderBatchPublication::test_publishes_the_healthy_member_when_a_batched_job_is_cancelled` | cancellation | Two model Jobs share a controlled render fence | Healthy output publishes; cancelled output has no storage pointer | E2E | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_refuses_empty_gpu_frames` | failure | Raw or finalized pixels with zero alpha | Existing CPU fallback selected | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_preserves_nonzero_alpha` | boundary | Only final pixel has alpha 1, 128 or 255 | Exact GPU pixels retained | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_requires_visibility_in_each_view` | multiview | One requested view is empty | Whole model falls back with view association preserved | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_geometry_wire.py::TestGeometryWire::test_preserves_geometry_identity` | protocol | Canonical, deferred or visual geometry | Parser framing and owner use matching identity | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_geometry_wire.py::TestGeometryWire::test_refuses_malformed_geometry_framing` | protocol refusal | Truncated body/header or invalid layout | Wire refused before use | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_geometry_wire.py::TestGeometryWire::test_owner_validates_framed_arrays` | array validation | Well-framed invalid coordinate/index | Owner refuses invalid arrays | unit | Verified |
| `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_publishes_the_renderer_frame` | publication | Valid full-render frame | Original validated image bytes stored | integration | Verified |
| `backend/tests/unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_invalid_generated_frames` | publication validation | Empty, wrong-sized or border-touching generated image | Validation refusal retained | unit | Verified |
| `backend/tests/unit/runtime/compute/test_client.py::TestConnect::test_withdraws_without_terminating_shared_owner` | cancellation | Caller withdraws while shared broker starts | Caller stops; shared process remains owned | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_bounds_readback_to_requested_frames` | response admission | Two requested supersampled views | IPC response allowance follows the requested frames before body allocation | unit | Verified |
| `backend/tests/unit/modules/media/test_mesh_render.py::TestAcceleratedEncoding::test_renders_cpu_fallback_after_compute_refusal` | fallback | Accelerated call refuses prepared geometry | Exactly one GPU attempt then nonempty CPU thumbnail | unit | Verified |

Validation after retirement: the initial serial selection reported **778 passed,
3 failed and 2 deselected in 278.29 seconds**. All three failures were in the
adapted framing test: it incorrectly expected the encoder's trusted-packet split
helper to implement the external decoder's byte validation. Only that test was
changed to call the actual decoder. Its complete file then passed **11/11 in
3.44 seconds**, including the three corrected cases; scoped test hygiene passed
**6/6 in 3.82 seconds**. The other 778 successful checks were not unnecessarily
repeated. The configured full Pyright gate reported zero errors and warnings;
Ruff passed on 24 affected files and the corrected test. `git diff --check` passed.
The existing Starlette/AnyIO deprecation warning was unchanged.

Commands from `backend`, executed serially for pytest:

```bash
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_compute_render.py \
  tests/unit/modules/media/test_compute_geometry_wire.py \
  tests/unit/modules/media/test_mesh_protocol.py \
  tests/unit/modules/media/test_native_budget.py \
  tests/unit/modules/media/test_thumbnail.py \
  tests/unit/modules/media/test_mesh_render.py \
  tests/unit/modules/media/test_worker_bootstrap.py \
  tests/unit/runtime/compute/test_client.py \
  tests/integration/modules/derivatives/test_producers.py \
  tests/integration/modules/media/test_native_budget.py \
  tests/integration/modules/media/test_thumbnail_engine.py \
  tests/integration/modules/media/test_mesh_isolation.py \
  tests/integration/modules/media/test_worker_bootstrap.py \
  tests/integration/modules/media/test_native_execution.py \
  tests/integration/runtime/test_preparation_runtime.py \
  tests/integration/bootstrap/test_native_resources.py \
  tests/repo/test_architecture.py tests/repo/test_mesh_boundaries.py \
  tests/e2e/test_render_batch_publication.py \
  -q -n 0 --tb=short \
  -m 'not slow and not coverage_gate and not scale and not postgres and not s3 and not remote_storage and not bgcode and not native_host'
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_compute_geometry_wire.py -q -n 0 --tb=short
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/repo/test_test_hygiene.py -k test_compute_geometry_wire -q -n 0 --tb=short
.venv/bin/pyright
```

## Archived evidence scope

Everything below describes the historical handoff implementation and its checks
at `3988d697`/`5814e6e7`, before retirement. Removed test names and commands are
preserved as historical evidence, not runnable instructions or current runtime
contracts. The active matrix above supersedes the retired-path coverage. The
publication, encoding and cancellation safeguards themselves remain active.

## Historical handoff implementation

A visual-only derivative whose conservative native estimate claims the whole memory pool can release its parser's native CPU/RAM permit before waiting for the compute broker and image encoding. The parser writes a bounded geometry packet and emits a distinct `PreparedThumbnailOutput`; it does not claim an image or completed preview. After a clean parser exit and descendant reap, the supervisor validates directory identity, regular-file ownership, size, digest, and the lightweight wire envelope. Array validation remains in the compute owner or isolated CPU fallback.

The same Job, scheduling priority, source identity, derivative attempt, recipe and publication fences remain authoritative. Fingerprint requests and bounded smaller estimates retain the existing single-worker path. Preflight runs once after materialization; an immutable plan binds the amount to the source, work profile and pool snapshot before optional visual-slot admission. A deployment without optional rendering, insufficient configured prepared-byte capacity, or geometry outside the transport ceiling retains the admitted CPU path. Transport refusal reuses already prepared geometry.

Staging has a four-slot, 192 MiB sublimit, and its packet plus three RGBA buffers are also charged to the existing prepared-source allowance. At 640×480 this is about 62.07 MiB per reservation, so the byte ceiling permits three concurrent staged packets. This does not increase the configured durable Job concurrency. Descriptor-held prepared and visual credits follow both guarded processes; disk capacity remains held until staged files are removed.

The GPU response is bounded before reading its body: acknowledgements are limited to 4 KiB and the image response to its exact final RGBA envelope. The handoff itself imports neither NumPy nor Pillow image operations; the existing publisher still decodes the completed thumbnail for validation. Successful GPU pixels go to a second isolated encoder; an unavailable or failed device sends the same geometry packet to isolated CPU rendering, without reloading the source. Both use the original deadline and the existing weighted native admission pool. Cancellation cannot become fallback or publication.

## Observability and costs

`mesh_thumbnail_handoff` correlates parser and finalizer execution IDs and records monotonic packet-read time, RPC dispatch time, finalizer admission wait, finalizer supervision, and exit cause. RPC duration is not GPU busy time. Finalizer failures retain their own supervision/RSS evidence rather than acquiring the successful parser's statistics.

A second disposable process adds startup cost per staged thumbnail, and encoding can wait behind an earlier native claim. These costs must be compared in the bounded deployment benchmark; the tests establish behavior, not a throughput improvement. The persistent broker still reuses its device and shaders. No forkserver or persistent parser was introduced.

## Verification

The focused suite passed **296 tests in 77.19 seconds**. A final follow-up after explicitly preserving the configured CPU face-chunk size passed **15 tests in 28.73 seconds**, covering staged custody and all publication E2E cases. The only warning was the existing Starlette/AnyIO deprecation. Ruff passed on the affected files; scoped Pyright passed for 14 production modules with zero errors or warnings.

The initial focused run found one test expectation error: the frame decoder deliberately exposes `invalid_mesh_output`, while the new test expected its internal ordering reason. The expectation was corrected and the complete focused suite rerun. The transport-limit fallback now consumes the already prepared representation, preventing an unnecessary second preparation of the same mesh.

Run from `backend` using the project interpreter (the environment did not have `uv` on PATH):

```bash
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_mesh_protocol.py \
  tests/unit/modules/media/test_compute_render.py \
  tests/unit/runtime/compute/test_client.py \
  tests/integration/runtime/test_preparation_runtime.py \
  tests/integration/modules/media/test_thumbnail_staging.py \
  tests/integration/modules/media/test_thumbnail_engine.py \
  tests/integration/modules/media/test_mesh_worker.py \
  tests/e2e/test_render_batch_publication.py \
  -q -n 0 --tb=short \
  -m 'not slow and not coverage_gate and not scale and not postgres and not s3 and not remote_storage and not bgcode and not native_host'

PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/integration/modules/media/test_thumbnail_staging.py \
  tests/e2e/test_render_batch_publication.py \
  -q -n 0 --tb=short \
  -m 'not slow and not coverage_gate and not scale and not postgres and not s3 and not remote_storage and not bgcode and not native_host'
```

The publication tests use the real API, Job, broker transport, parser, fallback worker, storage and fences, with a controlled external GPU executor. They do not qualify physical GPU quality or speed. No R2 request or corpus CPU benchmark was run for this test evidence. The candidate deployment benchmark and full repository gates are recorded separately.

## Coverage matrix

| Behaviour/test | Category | Precondition/input | Observable outcome | Tier | Status |
|---|---|---|---|---|---|
| `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailOutput::test_roundtrips_unpublished_visual_custody` | positive | Prepared descriptor + terminal frame, staging explicitly enabled | Receipt roundtrips; preview remains NOT_PRODUCED | unit | Verified |
| `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailOutput::test_requires_explicit_staging_acceptance` | boundary | Prepared descriptor sent to a normal decoder | Frame rejected before exposure | unit | Verified |
| `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailOutput::test_rejects_incompatible_staged_requests` | negative | Fingerprint requested or thumbnail not requested | Staging decoder refuses request | unit | Verified |
| `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailOutput::test_refuses_prepared_success_claims` | invariant | Prepared descriptor claims a completed image | Descriptor rejected | unit | Verified |
| `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailOutput::test_refuses_invalid_visual_receipts` | boundary | Boolean/oversized size or malformed SHA256 | Invalid receipt never exposed | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestStagedRender::test_keeps_owned_geometry_through_dispatch` | positive | Owned memoryview with original short deadline | Same body view, background priority, bounded deadline, associated pixels | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestStagedRender::test_leaves_rejected_pixels_for_isolated_fallback` | negative | Intermediate or truncated broker pixels | Returns fallback without supervisor postprocessing | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestStagedRender::test_propagates_staged_withdrawal` | cancellation | Cancelled staging render | Cancellation propagates instead of CPU fallback | unit | Verified |
| `backend/tests/unit/runtime/compute/test_client.py::TestRenderResponseBudget::test_rejects_oversized_responses_before_reading_body` | resource boundary | Oversized admission ack, geometry ack, or image response | Refuses length before reading the body | unit | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_dispatch_releases_parser_memory` | positive | Real parser + controlled GPU pixels + real encoder | RPC holds prepared/visual credits, no native credit; image publishes after encoding | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_fallback_uses_owned_geometry_after_source_removal` | fallback | Source removed after real parser finishes; GPU unavailable | CPU worker renders original packet; measured geometry retained | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_rejects_unaccepted_parser_completion` | failure | Inject rejection after streamed parser frames | Measurements only; no GPU call or thumbnail publication | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_withdrawal_prevents_thumbnail_publication` | cancellation | Withdrawal at GPU boundary | No thumbnail publication; no native permit retained | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_keeps_the_original_finalization_deadline` | deadline | Original deadline expires during dispatch | No RGBA file or thumbnail published; timeout remains explicit | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestStagedPacketOwnership::test_rejects_changed_custody` | custody | Digest change, symlink, or replaced staging directory | Receipt cannot authorize changed bytes | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestStagedPacketOwnership::test_cleanup_releases_visual_custody` | recovery | Exception with partial staged file | Owned directory deleted and visual credit released | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestStagedPacketOwnership::test_supervisor_keeps_numerical_imports_isolated` | import boundary | Fresh interpreter imports supervisor/finalizer | NumPy and PIL.Image stay unloaded | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestStagingBootstrap::test_transports_visual_credit_to_the_guarded_worker` | custody | Real guarded child with active visual permit | Child resolves inherited descriptor to the owned ledger receipt | integration | Verified |
| `backend/tests/integration/runtime/test_preparation_runtime.py::TestVisualStaging::test_requires_shared_prepared_bytes` | resource boundary | Visual request exceeds retained prepared allowance | Uncharged staging refused | integration | Verified |
| `backend/tests/integration/runtime/test_preparation_runtime.py::TestVisualStaging::test_applies_the_visual_byte_sublimit` | concurrency | Three 640px packets held; a fourth waits | 192MiB byte ceiling blocks fourth until custody releases | integration | Verified |
| `backend/tests/integration/runtime/test_preparation_runtime.py::TestVisualStaging::test_inherited_visual_descriptor_retains_credits` | custody | Owner scope exits while duplicated FD survives | Credits stay locked until inherited descriptor closes | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_engine.py::TestVisualPreparation::test_emits_owned_geometry_before_rendering` | positive | Real STL parser, image renderer forbidden | Measurements plus bounded packet emitted; no completed preview claimed | integration | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_engine.py::TestVisualPreparation::test_preserves_rendering_when_transport_declines_geometry` | fallback | Optional geometry transport budget refusal | Existing isolated render still produces thumbnail | integration | Verified |
| `backend/tests/e2e/test_render_batch_publication.py::TestStagedThumbnailPublication::test_publishes_from_owned_geometry_after_gpu_failure` | headline E2E | API ingest + real Job/parser/broker, controlled external device failure | Same-packet CPU fallback publishes fenced metadata/image; original bytes/measurements preserved; all resource capacity recoverable | E2E | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_preserves_failing_finalizer_supervision` | failure attribution | Inject finalizer memory refusal after real parser exits | Error retains failing finalizer execution/RSS, not successful parser statistics | integration | Verified |


## Publication and encoder follow-up

The first candidate retained a publication seam that could crop and encode an already
rendered thumbnail again. The follow-up preserves the complete renderer's frame
while retaining decode, pixel-budget, dimensions, alpha and border validation.
Embedded previews retain normalization. Necessary encoding uses the shared visual
profile. The disposable encoder reads its explicit request without importing the
application configuration or supervisor; process custody and reply limits remain
unchanged. Broker launch waits also honor cooperative cancellation. Selective staging avoids a second process for known geometry that fits a weighted share; no native-memory coefficient is reduced.

The serialized follow-up passed **409 tests in 134.28 seconds**, with two cases
excluded by the established resource-lane marker selection and only the existing
Starlette/AnyIO warning. This includes both architecture guards, the preserved
visual-input recipe fixture, complete producer coverage and publication E2E.
Scoped Pyright passed for ten production modules with zero errors or warnings;
Ruff passed on 22 affected files and `git diff --check` was clean. The original
296-test evidence above predates these changes.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 26 | preserves generated framing | Happy | Valid rendered WebP with less than 76% alpha extent | Original encoded bytes returned | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_preserves_generated_framing` |
| 27 | encodes generated PNG without reframing | Edge | Valid generated PNG with transparent border | Decoded RGBA unchanged; shared codec bytes | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_encodes_generated_png_without_reframing` |
| 28 | rejects invalid generated frames | Error | Empty, wrong-size, alpha-free or border-touching image | Stable validation failure | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_invalid_generated_frames` |
| 29 | rejects oversized generated declarations | Error | PNG declaring more than pixel limit | Refusal before decoded image allocation | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_oversized_generated_declarations` |
| 30 | retains embedded normalization | Edge | Small embedded preview | Canonical dimensions and normalized alpha extent | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_embedded_preview_is_centered_on_the_canonical_canvas` |
| 31 | publishes the renderer's frame | Happy | Full-render output delivered through derivative producer | Stored bytes equal validated renderer output | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_publishes_the_renderer_frame` |
| 32 | isolates encoder dependencies | Edge | Fresh disposable encoder interpreter | Exact image with no configuration, Pydantic or numerical imports | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestStagingBootstrap::test_encoder_avoids_application_configuration` |
| 33 | withdraws during launch-lock contention | Error | Existing launch lock unavailable | Cancellation escapes promptly; socket closed | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestConnect::test_withdraws_during_launch_lock_contention` |
| 34 | withdraws without terminating a shared owner | Error | Broker starting when requesting Job withdraws | Request cancelled; shared child not terminated | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestConnect::test_withdraws_without_terminating_shared_owner` |
| 35 | retains original bootstrap deadline | Edge | Caller deadline expires during lock wait | Explicit deadline refusal without extending the deadline | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestConnect::test_retains_original_bootstrap_deadline` |
| 36 | rejects malformed generated bytes | Error | Non-image payload marked generated | Decode validation fails | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_malformed_generated_bytes` |
| 37 | avoids retrying compute during CPU fallback | Error | GPU forbidden after staging refusal | CPU facade returns a nonempty image | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_render.py::TestAcceleratedEncoding::test_renders_cpu_fallback_without_retrying_compute` |
| 38 | identifies whole-pool custody | Edge | Known small, unknown, saturated large complexity | Staging eligibility follows the unchanged conservative allowance | Unit | ✅ `backend/tests/unit/modules/media/test_native_budget.py::TestNativePlan::test_identifies_whole_pool_custody` |
| 39 | rejects changed execution contracts | Error | Pool, source or profile differs after preflight | Stale plan cannot authorize execution | Unit | ✅ `backend/tests/unit/modules/media/test_native_budget.py::TestNativePlan::test_rejects_changed_execution_contract` |
| 40 | reuses one preflight | Happy | Prepared source with immutable admission plan | Real guarded parser/finalizer succeed while any second preflight is forbidden | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestNativePlan::test_reuses_preflight_for_guarded_execution` |
| 41 | retains one worker for bounded geometry | Happy | Known small STL with optional compute enabled | Metadata and thumbnail published without visual-slot acquisition | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_retains_one_worker_for_bounded_geometry` |

The staged fallback headline uses a small 3MF whose preflight remains unknown, so
it exercises whole-pool handoff without enlarging the fixture or lowering native
budgets. The controlled renderer now produces a transparent border, matching the
frame contract instead of relying on publication to correct a fully opaque canvas.


Follow-up command from `backend`:

```bash
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_thumbnail.py \
  tests/unit/modules/media/test_native_budget.py \
  tests/unit/modules/media/test_mesh_render.py \
  tests/unit/runtime/compute/test_client.py \
  tests/integration/modules/media/test_thumbnail_staging.py \
  tests/integration/modules/media/test_native_budget.py \
  tests/integration/modules/derivatives/test_producers.py \
  tests/integration/api/v1/ingest/test_ingest_api.py::TestIngestModel::test_force_rebuild_refreshes_existing_mesh_thumbnail \
  tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes \
  tests/repo/test_architecture.py tests/repo/test_mesh_boundaries.py \
  tests/e2e/test_render_batch_publication.py \
  -q -n 0 --tb=short \
  -m 'not slow and not coverage_gate and not scale and not postgres and not s3 and not remote_storage and not bgcode and not native_host'
```

The CI architecture failure was resolved by injecting the finalizer runner and
pool instead of importing the supervisor back into its continuation. Shared error
types and reply limits have independent owners. The disposable fallback worker
uses the application's renderer facade with compute explicitly disabled; it is
covered by the same guardian/permit tests and is the only new raw-consumer owner.
The work-profile observer now also checks the explicit deadline/plan arguments.


## Empty accelerated frames

A response with zero alpha in every pixel now leaves rendering to the existing
CPU fallback. It does not reach source-validation failure or classify the Artifact
as invalid. Every requested view must contain nonzero alpha; one translucent pixel
is sufficient. The check uses a strided memory view and stops at the first visible
pixel without allocating another image or alpha channel. Publication retains its
independent final image validation.

| Behaviour / test | Category | Precondition / input | Observable outcome | Tier | Status |
|---|---|---|---|---|---|
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_refuses_empty_gpu_frames` | failure | Raw or finalized GPU response has nonzero RGB but zero alpha | Caller selects CPU fallback | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_preserves_nonzero_alpha` | boundary | Only the final pixel has alpha 1, 128 or 255 | Accelerated pixels retained exactly | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestOptionalRender::test_requires_visibility_in_each_view` | multiview | First or second view is empty | Complete model request falls back without reassigning views | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestStagedRender::test_keeps_owned_geometry_through_dispatch` | custody | Owned packet and one nonzero-alpha pixel | Same immutable geometry and exact visible response retained | unit | Verified |
| `backend/tests/unit/modules/media/test_compute_render.py::TestStagedRender::test_leaves_rejected_pixels_for_isolated_fallback` | failure | Empty, truncated or unprocessed staged response | Isolated finalizer receives fallback responsibility | unit | Verified |
| `backend/tests/e2e/test_render_batch_publication.py::TestStagedThumbnailPublication::test_publishes_from_owned_geometry_after_gpu_failure` | publication | Real Job/parser/broker with external device error or transparent GPU response | Same-packet CPU fallback publishes thumbnail and metadata; original retained and all resource credits recoverable | E2E | Verified |

The final guard selection passed **63 tests in 41.21 seconds**, including both
publication outcomes above, with only the existing Starlette/AnyIO warning.
Ruff and `git diff --check` passed. The configured full Pyright gate passed with
zero errors and warnings after this patch.

```bash
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_compute_render.py \
  tests/unit/modules/media/test_mesh_render.py \
  tests/integration/modules/media/test_thumbnail_staging.py \
  tests/e2e/test_render_batch_publication.py \
  -q -n 0 --tb=short \
  -m 'not slow and not coverage_gate and not scale and not postgres and not s3 and not remote_storage and not bgcode and not native_host'
```

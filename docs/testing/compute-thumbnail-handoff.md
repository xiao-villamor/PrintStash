# Thumbnail preparation handoff

A visual-only derivative can release its parser's native CPU/RAM permit before waiting for the compute broker and image encoding. The parser writes a bounded geometry packet and emits a distinct `PreparedThumbnailOutput`; it does not claim an image or completed preview. After a clean parser exit and descendant reap, the supervisor validates directory identity, regular-file ownership, size, digest, and the lightweight wire envelope. Array validation remains in the compute owner or isolated CPU fallback.

The same Job, scheduling priority, source identity, derivative attempt, recipe and publication fences remain authoritative. Fingerprint requests retain the existing path. A deployment without optional rendering, insufficient configured prepared-byte capacity, or geometry outside the transport ceiling retains the admitted CPU path. Transport refusal reuses already prepared geometry.

Staging has a four-slot, 192 MiB sublimit, and its packet plus three RGBA buffers are also charged to the existing prepared-source allowance. At 640×480 this is about 62.07 MiB per reservation, so the byte ceiling permits three concurrent staged packets. This does not increase the configured durable Job concurrency. Descriptor-held prepared and visual credits follow both guarded processes; disk capacity remains held until staged files are removed.

The GPU response is bounded before reading its body: acknowledgements are limited to 4 KiB and the image response to its exact final RGBA envelope. The supervisor imports neither NumPy nor Pillow image operations. Successful GPU pixels go to a second isolated encoder; an unavailable or failed device sends the same geometry packet to isolated CPU rendering, without reloading the source. Both use the original deadline and the existing weighted native admission pool. Cancellation cannot become fallback or publication.

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
| `backend/tests/e2e/test_render_batch_publication.py::TestStagedThumbnailPublication::test_publishes_from_owned_geometry_after_device_failure` | headline E2E | API ingest + real Job/parser/broker, controlled external device failure | Same-packet CPU fallback publishes fenced metadata/image; original bytes/measurements preserved; all resource capacity recoverable | E2E | Verified |
| `backend/tests/integration/modules/media/test_thumbnail_staging.py::TestThumbnailHandoff::test_preserves_failing_finalizer_supervision` | failure attribution | Inject finalizer memory refusal after real parser exits | Error retains failing finalizer execution/RSS, not successful parser statistics | integration | Verified |

# PRI-16 GPU thumbnail pipeline review

## Findings

The production pipeline does not yet execute the GPU candidate. Derivative
producers reserve/materialize the source, call mesh_isolation.generate, and
publish validated output from the supervised mesh worker. ThumbnailEngine
prefers embedded thumbnails, otherwise invokes mesh_render's CPU rasterizer,
with bounded STL streaming recovery. The wgpu adapter is currently a research
script; there is no production GPU admission, GPU worker selection or GPU retry.
The browser GPU preference does not configure server thumbnail rendering.

The earlier whole-flow estimate must not be summarized as slower GPU rasterization.
On the recorded real medium STL, median render plus postprocessing was 717 ms
on CPU and 223 ms through the hybrid GPU adapter. Cold GPU setup added about
349 ms for context/pipeline creation and 150 ms for frame setup in the experimental
WSL driver. That consumed the rendering saving. These are development samples,
not isolated production Job latency.

The large 3MF spent 3,022 ms in shared import/load/preparation and about 753 ms
in CPU render/postprocessing. Removing all render/postprocessing cost from the
recorded observations yields a median optimistic speedup ceiling of 1.248x.
The original 1.5x complete-flow target for this source therefore also requires
preparation improvements. This bound applies to the measured estimate, not a
claim about every 3MF or a real upload Job.

## Confirmed selection defect

Default selection eagerly evaluated every enumerated backend before choosing
one. A later backend failure could prevent use of already eligible hardware.
Stopping at the first eligible adapter fixes that error case, but measured
real-file performance did not substantially improve.

A detailed three-cycle trace then placed about 500 ms inside native enumeration
after a rendered session closes. Python capability checks took less than 0.02 ms.
An isolated discovery probe that retained adapter references between iterations
had much lower enumeration cost and did not represent that lifecycle.

The final selection path asks WebGPU for the platform's high-performance adapter,
checks hardware identity and required limits, and only enumerates other adapters
when the preference fails or is incompatible. Enumeration fallback stops at the
first compatible adapter. Explicit selection still enumerates candidates and
refuses ambiguity. Failed preference diagnostics are retained if fallback also
finds no eligible adapter. This changes default selection to the platform
preference; there is no vendor whitelist.

The focused adapter/diagnostic selection passes 104 tests; Ruff and explicit
adapter Pyright checks pass. Native performance evidence identifies the exact
tested commit separately.

## Other costs and integration constraints

- The candidate is hybrid: GPU coverage/depth selects face identifiers, then
  CPU code sorts identifiers, interpolates original-precision normals and shades
  pixels. Canonical postprocessing and WebP encoding remain on the CPU.
- Face identifiers currently occupy an RGBA32Float attachment. At 1280x960,
  final raw readback transfers 19,660,800 bytes although one integer per pixel
  would need 4,915,200. A scalar integer attachment is a separate worthwhile
  optimization, with allocation/readback tests and unchanged quality gates.
- Each chunk currently waits for submitted GPU work. A diagnostic removing
  intermediate waits preserves pixels in the tested cases but cannot simply
  replace the bounded implementation: queued write-buffer staging must remain
  bounded and accounted. Submission time must not be mislabeled execution time.
- A fresh device/pipeline for every disposable Job has a different cost from
  reusing one within a multiview invocation. A persistent GPU worker pool would
  require the separate lifecycle proposal called for by the original plan.
- Canonical already-normalized WebP output has an existing validation/encode
  bypass in thumbnail.to_webp; assuming unconditional double encoding would be
  an incorrect diagnosis.

## Integration recommendation

Keep the existing derivative scheduling, source admission, geometry publication,
embedded-thumbnail preference, storage publication and streaming fallbacks.
Backend choice belongs in the media rendering owner inside the native worker.
Create a GPU session only after a source actually needs rasterization; reuse it
for all views in that invocation and release it before releasing admission.

Any future persistent worker design must reserve one execution slot per device,
account for idle driver/context memory, bound queued source ownership, retire
per-Job geometry and targets, and kill/recreate the process on cancellation,
deadline expiry or device loss before CPU recovery. CPU retry keeps the original
deadline. This proposal does not enable a pool or waive hardware/quality gates.

Before adoption: correct current coverage/depth quality failures; measure the
revised selector with real files; separate startup, adapter/device/pipeline,
render, readback, CPU shading, encoding and publication costs; qualify preview
and multiview independently on both required devices. A render-only win cannot
qualify upload-to-thumbnail latency.

## Change coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | uses eligible device before unavailable later backend | Error | First eligible adapter, later failing backend | Ready adapter remains usable | Integration | ✅ TestNativeSelection.test_uses_eligible_device_before_unavailable_later_backend |
| 2 | selects first compatible hardware | Edge | Software or insufficient attachment/attribute limits first | Compatible physical adapter selected | Integration | ✅ TestNativeSelection.test_selects_first_compatible_hardware |
| 3 | preserves explicit selection | Happy | Unique requested adapter after another candidate | Requested adapter selected | Integration | ✅ TestNativeSelection.test_preserves_explicit_selection |
| 4 | refuses nonunique explicit selection | Error | Empty, missing or duplicate eligible names | Typed capability refusal | Integration | ✅ TestNativeSelection.test_refuses_nonunique_explicit_selection |
| 5 | refuses absent compatible hardware | Error | Only software adapter without opt-in | Typed capability refusal | Integration | ✅ TestNativeSelection.test_refuses_absent_compatible_hardware |
| 6 | labels opted-in software execution | Edge | Software diagnostic opt-in | Physical acceleration remains false | Integration | ✅ TestNativeSelection.test_labels_opted_in_software_execution |
| 7 | uses capability fallback after preference failure | Error | Preferred adapter request fails | Eligible enumerated adapter usable | Integration | ✅ TestNativeSelection.test_uses_capability_fallback_after_preference_failure |
| 8 | retains preference failure when no adapter is eligible | Error | Failed preference, no fallback | Original diagnostic retained as refusal cause | Integration | ✅ TestNativeSelection.test_retains_preference_failure_when_no_adapter_is_eligible |
| 9 | uses platform preference before enumeration | Happy | Preferred adapter differs from first enumerated | Eligible platform preference selected | Integration | ✅ TestNativeSelection.test_uses_platform_preference_before_enumeration |
| 10 | keeps explicit selection independent of platform preference | Edge | Explicit choice, failed preference API | Explicit adapter remains usable | Integration | ✅ TestNativeSelection.test_keeps_explicit_selection_independent_of_platform_preference |
| 11 | stops fallback before unavailable later backend | Error | Incompatible preference, eligible fallback, later failing driver | Eligible fallback remains usable | Integration | ✅ TestNativeSelection.test_stops_fallback_before_unavailable_later_backend |

Tests live in backend/tests/integration/scripts/test_wgpu_render_backend.py.
This focused change does not complete the original production delivery matrix.

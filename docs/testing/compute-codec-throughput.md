# Codec scheduling and preview throughput

Work in progress. The comparison uses the same immutable 1000-source cohort,
submission policy, instrumentation and machine for each arm. Accumulated phase
times overlap and are not wall-time components. No performance result is claimed
before the measured arms complete.

## Diagnosis from the previous run

The report's 48 MiB bound is the maximum per-model geometry packet and its
canonical recovery envelope. It is not a total VRAM limit. The broker already
has a 1 GiB device budget; only six large STL inputs in that cohort exceeded the
packet envelope. Raising the packet bound alone would enlarge every retained
host reservation: the existing 384 MiB/four-owner host window would admit two
owners at 128 MiB packets and one at 256 MiB, rather than four. That can reduce
throughput even on a GPU with spare memory.

The larger observed delay was codec admission: 1295.678 accumulated seconds of
queueing versus 132.525 seconds encoding. A one-slot/one-byte encoder claim sat
in the parser's strict FIFO pool behind claims for all native RAM. The fix gives
completed frames one process-shared codec slot, additional to parser slots,
while retaining their existing host-memory credit until encoding finishes.

The 29.652 accumulated broker seconds are a small part of the previous run.
Source loading accumulated 1361.014 seconds; worker supervision includes startup,
loading and shutdown. These intervals overlap across jobs. GPU mapping includes
a completion fence, so the old transfer counter is not pure PCIe transfer time.

## Encoding-only pilot

A deterministic sample of 200 saved frames from the previous run was decoded
once per frame. Four encoding arms rotated order; decode and resize are outside
the encoder timer. The 512-pixel arms downsampled existing images; these are not
new render measurements. All 800 outputs preserved their respective input alpha.

| Encoding | Dimensions | Accumulated encoding seconds | Encoded bytes |
| --- | --- | ---: | ---: |
| Lossless, quality 80, method 2 | 640×480 | 18.363 | 7,358,800 |
| Lossy, quality 85, method 2 | 640×480 | 2.554 | 2,264,548 |
| Lossy, quality 85, method 2 | 512×384 | 2.086 | 1,895,100 |
| Lossy, quality 85, method 0 | 512×384 | 1.807 | 4,586,222 |

Quality 85/method 2 was selected for the pipeline trials. Across sampled frames,
maximum frame-mean visible RGB absolute error was 3.93/255 at 512×384. This is a
numerical quality check, not a claim of human visual approval.

## Integration with current main

The measured candidate source is `08c759779a350243537587e623183f6522ae22b7`.
The immutable images remain unchanged while current main is integrated into the
PR. That integration preserves renderer provenance validation from main, exposes
its alpha-quality metadata, keeps both historical visual fixtures and adds v3
for quality 85. G-code thumbnail recipe becomes 3 because main already published
recipe 2 at quality 90. These runs use STL/OBJ/STEP only. The numbers describe the
recorded image commits, not a new measurement of subsequent main integration.

## Matched run environment

Commands and Docker orchestration execute inside WSL2 as Linux user `local`.
Linux 6.18.40.1-microsoft-standard-WSL2 exposes a Ryzen 5 1600 (6 cores/12 threads),
7909 MiB RAM and 2048 MiB swap. The detected adapter is an NVIDIA RTX 5060 with
8151 MiB VRAM, driver 610.88. This differs from the initially described 6 GB.

Every arm uses the frozen 1000-source selection, verified local cache, fresh
local storage, an internal Docker network, cold first four outputs, then a
continuous window of 40 unfinished Artifacts with four upload/ingestion slots.
No R2 acquisition is included. Same dependency image and tracing in every arm.
Baseline uses the prior measured image; candidate changes application/core
source only. GPU policy is explicitly preview, device budget 1024 MiB, render
concurrency four. Candidate CPU uses the same 512×384/lossy profile as candidate
GPU. Default product width remains 640; 512 is an existing supported setting.

The tracer records thread CPU and monotonic spans, resource admission with file
lock wait/hold, actual socket bytes/calls/timeouts, GPU host submission, mapped
readback and GPU fence waits. A 250 ms process/cgroup sampler records RSS, CPU,
I/O and memory pressure; a one-second physical adapter sampler records utilization
and VRAM. Physical GPU memory includes other Windows users; summed process RSS
can count shared pages more than once. Neither is per-process dedicated VRAM.
Publication uses separate WSL monotonic and UTC clocks. No Windows process is
used to run the benchmark or collect a host performance counter.

## Behavior coverage

Paths below are relative to `backend/`.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | encodes while a parser owns the native pool | Happy | GPU frame; full parser reservation | Image completes before parser release | Integration | ✅ `integration/modules/media/test_thumbnail_handoff.py::TestCodecProgress::test_encodes_while_a_parser_owns_the_native_pool` |
| 2 | bounds simultaneous codecs | Concurrency | Two prepared owners | Only one codec active | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_bounds_simultaneous_codecs` |
| 3 | requires host custody | Error | No host permit | Admission refused | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_requires_host_custody` |
| 4 | refuses native credit during codec admission | Error | Parser permit held | Reversed admission refused | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_refuses_native_credit_during_codec_admission` |
| 5 | refuses nested codec admission | Error | Codec already active | Recursive admission refused | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_refuses_nested_codec_admission` |
| 6 | releases codec credit after withdrawal | Error | Encoder raises cancellation | Next encoder enters | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_releases_codec_credit_after_withdrawal` |
| 7 | withdraws a waiting codec | Cancellation | Another encoder owns slot | Cancelled waiter exits; subsequent admission succeeds | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_withdraws_a_waiting_codec` |
| 8 | compresses preview pixels with bounded visible error | Happy | Spatially coherent detailed frame | Under 60% of lossless bytes; RGB mean absolute error below 5/255 | Core unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_compresses_preview_pixels_with_bounded_visible_error` |
| 9 | preserves decoded alpha | Edge | Transparent and partial alpha | All alpha bytes exact | Core unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_decoded_alpha` |
| 10 | preserves exact PNG pixels | Regression | RGBA frame | PNG round trip exact | Core unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_exact_png_pixels` |
| 11 | preserves generated framing | Regression | Generated WebP | Publication retains encoded bytes | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_preserves_generated_framing` |
| 12 | retains full geometry with smaller previews | Happy | Real STL; width 512 | Original bytes/triangle count retained; complete 512×384 lossy preview | E2E | ✅ `e2e/test_ingest.py::TestMetadata::test_retains_full_geometry_with_smaller_previews` |
| 13 | rederives thumbnails from the previous GPU recipe | Upgrade | Old ready/failed mesh thumbnail | Normal source finds stale recipe | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_rederives_thumbnails_from_the_previous_gpu_recipe` |
| 14 | keeps previous thumbnail visible during recipe backfill | Upgrade | Old published thumbnail | Existing pointer retained until replacement | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_keeps_previous_thumbnail_visible_during_recipe_backfill` |
| 15 | rederives G-code thumbnails from previous recipes | Upgrade | Old G-code thumbnail | Normal source finds stale recipe | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_rederives_gcode_thumbnails_from_previous_recipes` |
| 16 | preserves frozen visual input hashes | Regression | Versioned tetrahedron fixture | Six analytic hashes unchanged; new thumbnail/recipe identity pinned | Integration | ✅ `integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes` |
| 17 | counts partial socket transfers | Instrumentation | Short sends/receives | Actual bytes/calls measured | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestTransportTrace::test_counts_partial_socket_transfers` |
| 18 | preserves socket timeout | Instrumentation error | Socket timeout | Exception retained; timeout counted | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestTransportTrace::test_preserves_socket_timeout` |
| 19 | emits transfer bytes without payload | Instrumentation privacy | Transfer containing private payload | Only size/timing emitted | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestTransportTrace::test_emits_transfer_bytes_without_payload` |
| 20 | observes real admission without changing custody | Instrumentation | Actual file-lock resource pool | Permit retained; released capacity reusable | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestPoolTrace::test_observes_real_admission_without_changing_custody` |
| 21 | preserves cancellation during admission | Instrumentation error | Withdrawn claim | No admission; original exception preserved | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestPoolTrace::test_preserves_cancellation_during_admission` |

Local validation: 330 core/search tests; 116 ingestion/derivative/custody tests;
160 affected media/custody/tracer tests. Selections overlap and are not summed.
The broader media run was 2352 passed/9 failed; four failures were obsolete
lossless assertions, five observed different profile bytes across parent/child
while the profile was being edited. All affected cases passed in the fixed-source
160-test rerun. Ruff and the scoped core production format check pass.

## Planned observations

Each arm retains per-request stages, CPU time, parser/codec admission, resource
coordinator wait and hold, socket transfer bytes/time/backpressure, concurrency,
process RSS, and physical adapter memory. GPU completion waits are separate from
host socket copying. Device allocation counters are estimates, not VRAM readings.

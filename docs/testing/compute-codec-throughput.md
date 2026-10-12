# Codec scheduling and preview throughput

Three complete, sequential 1000-source runs were performed inside WSL as Linux
user `local`. The codec fix works, but this workload still has **no demonstrated
end-to-end GPU advantage at 512×384**: optimized CPU finished in **36.44 minutes**,
optimized GPU in **37.56 minutes**, and the previous GPU pipeline in **39.63
minutes**, using UTC elapsed time. The optimized GPU arm was **5.2% faster than
its baseline**, while optimized CPU was **3.0% faster than optimized GPU**.
These are single-run observations on this host, not statistical qualification.

Accumulated codec waiting fell **97.2%**, from 1273.299 to 35.085 seconds; its p95
fell from 5.126 to 0.104 seconds. The 973 GPU thumbnails use **75.5% fewer bytes**.
CPU loading, process lifecycle and admission/control work remain much larger than
GPU execution. Increasing VRAM alone does not address those costs.

All three runs preserved all **1000 original files**, produced **973 complete
metadata/thumbnail pairs**, and retained the same **27 invalid-source failures**.
All nine authoritative geometry fields match exactly for all 973 valid inputs;
all 1000 metadata rows and terminal derivative contracts match the baseline.
No geometry was simplified. [Complete sanitized aggregates](compute-codec-1000-summary.json)
include stage percentiles, thread CPU, concurrency, resource admission, memory,
I/O, transfer counters and job timing distributions.

## End-to-end comparison

| Measurement | Baseline GPU 640 | Optimized GPU 512 | Optimized CPU 512 |
| --- | ---: | ---: | ---: |
| Publication seconds, WSL monotonic | 2,239.463 | 2,109.777 | 2,044.378 |
| Publication seconds, UTC | 2,377.756 | 2,253.508 | 2,186.634 |
| Cold first four, UTC seconds | 15.566 | 20.361 | 10.516 |
| Remaining 996, UTC seconds | 2,362.165 | 2,228.240 | 2,176.046 |
| Cold first four GPU completions | 4 | 0 | 0 |
| All 973 thumbnails, MiB | 34.44 | 8.43 | 9.18 |
| Handoff codec sample count | 963 | 956 | 0 |
| Codec queue, accumulated seconds | 1,273.299 | 35.085 | — |
| Codec queue p95, seconds | 5.126 | 0.104 | — |
| Handoff encoding, accumulated seconds | 125.980 | 42.448 | — |
| Native admission wait, accumulated seconds | 2,631.102 | 1,954.980 | 2,858.687 |
| Container RAM sampled peak, MiB | 3,076.8 | 2,945.8 | 2,661.5 |
| Largest supervised child tree RSS, MiB | 1,000.8 | 1,000.8 | 883.7 |
| Container swap sampled peak, MiB | 12.4 | 75.9 | 45.8 |
| Container CPU, core-seconds | 6,682.0 | 6,574.2 | 6,794.9 |
| Physical adapter VRAM sampled peak, MiB | 1,265 | 1,248 | 1,059 |


The CPU renderer encodes inline. A missing handoff codec measurement is not zero
encoding work: CPU rasterization and encoding are included in its render phase.
The optimized GPU consumed about 3.2% fewer container CPU core-seconds than the
optimized CPU arm, but still finished later. Faster GPU work does not eliminate
its surrounding host/control overhead.

Monotonic and UTC elapsed time differ by 6.18%, 6.81% and 6.96% respectively.
Both clocks are retained, without applying a correction factor. Stage spans use
WSL monotonic time; job timestamps use database UTC; CPU clocks are separate.
Accumulated and nested stage durations overlap and must not be summed into a
wall-time total. The remaining-996 timings are descriptive subcohorts, not new
1000-model results; the CPU arm was also faster in that subcohort.

## Stage timings

| Measurement | Baseline GPU 640 | Optimized GPU 512 | Optimized CPU 512 |
| --- | ---: | ---: | ---: |
| Supervised mesh executions | 1,000 | 1,000 | 1,000 |
| Child supervision, accumulated seconds | 3,433.733 | 3,435.837 | 4,007.056 |
| Mesh load, accumulated seconds | 1,345.392 | 1,332.182 | 1,340.172 |
| Exact measurements, accumulated seconds | 113.348 | 114.355 | 109.661 |
| Child render phase, accumulated seconds | 78.672 | 96.571 | 578.864 |
| Broker connection setup, accumulated seconds | 792.760 | 821.701 | — |
| Render IPC exchange errors | 0 | 4 | — |
| Render IPC exchange, accumulated seconds | 354.043 | 379.676 | — |
| Broker execution, accumulated seconds | 29.350 | 28.332 | 0.000 |
| GPU map/fence wait, accumulated seconds | 4.072 | 3.928 | — |
| GPU mapped readback copy, accumulated seconds | 0.052 | 0.053 | — |
| Prepared packet read, accumulated seconds | 6.726 | 7.185 | — |
| Prepared packet write, accumulated seconds | 6.623 | 6.770 | — |


The optimized GPU spent only 28.332 accumulated seconds in broker execution,
versus 1332.182 in mesh loading, 3435.837 under child supervision, 1954.980 waiting
for native admission and 821.701 in broker connection setup. These overlap.
Supervision includes startup, parsing, work and shutdown; its difference from
named phases is not an isolated import-time measurement. GPU execution versus
the CPU render-phase total is not a pure renderer speedup ratio because the
paths contain different preparation, fallback and encoding work.

After the timed runs, 30 isolated calls each measured warm runtime/profile
identity calculation at a median **6.7 ms**. This does not explain the full
in-run connection-setup duration by itself. The next useful profiling split is
cancellation/database checkpoints, actual socket connection, profile validation
and thread scheduling inside that control path. Separately, measure child
startup and lazy imports before considering worker reuse; retain isolation and
hard memory limits. A bounded preflight improvement could reduce conservative
unknown-complexity claims, but the observed RSS is not permission to lower their
virtual-memory ceilings.

## Admission, locking and concurrency

| Measurement | Baseline GPU 640 | Optimized GPU 512 | Optimized CPU 512 |
| --- | ---: | ---: | ---: |
| Native admissions | 1,963 | 1,000 | 1,000 |
| Parser-only native wait, accumulated seconds | 1,376.752 | 1,954.980 | 2,858.687 |
| Legacy codec native wait, accumulated seconds | 1,254.350 | — | — |
| Whole-native-memory claims | 193 | 184 | 181 |
| Native memory-denied polls | 55,812 | 41,249 | 57,573 |
| Native slot-denied polls | 0 | 0 | 0 |
| Native FIFO-denied polls with spare capacity | 6,578 | 4,345 | 7,522 |
| Native coordinator entry, accumulated seconds | 657.100 | 458.414 | 755.181 |
| Native coordinator hold, accumulated seconds | 363.189 | 309.760 | 416.811 |
| Native slots active, time-weighted mean | 1.60 | 1.63 | 1.96 |
| Native slots active at peak | 4 | 4 | 4 |
| Thumbnail host claims at peak, MiB | 345.1 | 313.0 | — |
| Codec slots active at peak | — | 1 | — |
| Mesh job queue p95, UTC seconds | 15.897 | 14.164 | 16.496 |
| Mesh job execution p95, UTC seconds | 17.471 | 16.355 | 17.723 |


A denial counts a repeated poll, not a model or an independent failure. The
native pool orders admission within priority, with bounded promotion for old
backfill work. A later small claim can wait despite fitting the remaining
capacity. All measured native denials were memory or ordering related; none
were denied solely for exceeding the slot count.

The old native pool includes codec claims; parser-only waiting is separated
above. The new process-shared codec permit allows one encoder in addition to
parser slots, retaining its existing host-memory credit. At 512×384 the host
claim is 78.25 MiB per owner, versus 86.27 MiB at 640×480; four owners still fit
within the 384 MiB host window. All cgroup OOM and OOM-kill counters stayed zero.

## Transport and device work

| Measurement | Baseline GPU 640 | Optimized GPU 512 | Optimized CPU 512 |
| --- | ---: | ---: | ---: |
| IPC sent, MiB | 2,334.2 | 1,921.5 | — |
| IPC send-call elapsed seconds | 41.793 | 38.581 | — |
| IPC sent MiB / send-call second | 55.85 | 49.80 | — |
| IPC received, MiB | 2,339.5 | 1,926.8 | — |
| IPC receive-call elapsed seconds | 919.707 | 908.557 | — |
| IPC send timeouts | 2 | 2 | — |
| IPC receive timeouts | 2,843 | 2,754 | — |
| GPU frames | 967 | 963 | 0 |
| GPU render batches | 945 | 918 | 0 |
| Geometry device-upload bytes, MiB | 1,012.1 | 1,010.9 | 0.0 |
| GPU residency hits | 0 | 0 | 0 |
| Physical adapter utilization mean, % | 0.08 | 0.07 | 0.00 |
| Physical adapter utilization p95, % | 0.0 | 0.0 | 0.0 |


IPC counts aggregate sent or received bytes across both protocol endpoints,
including control traffic. Send-call throughput is effective application IPC
throughput under this workload, not PCIe bandwidth. Receive-call time includes
waiting for producers and readiness-poll timeouts. A polling timeout is not a
failed render. GPU mapping includes a completion fence; see measurement limits
below. One-second physical-adapter utilization can miss short bursts and includes
unrelated host activity, including during the CPU arm.

Of 918 optimized GPU batches, 878 contained only one model. Unique-source
residency hits were zero. A larger device cache has little opportunity to help
this single-use workload, and a larger geometry packet can reduce host-owner
concurrency. Keep the current memory bounds; the evidence favors CPU/control-path
work before another VRAM increase. The benchmark does not create a performance
qualification receipt or enable GPU routing for ordinary installations.

## Output audit

All 2919 saved thumbnails decoded as RGBA, had nonempty alpha, transparent
corners and no alpha touching an image edge. All 973 baseline thumbnails were
640×480 lossless WebP; both optimized sets were 512×384 lossy WebP. GPU files total
8,837,272 bytes and CPU files 9,623,536 bytes, versus 36,116,428 baseline bytes.
The two renderers are not pixel-identical; identical profile/settings do not
imply identical file sizes or alpha silhouettes. Exact alpha preservation is
verified against encoder input in tests and the encoding-only pilot, not by
claiming CPU and GPU alpha masks are identical.

Every original was downloaded after publication and checked for SHA-256 and
size against the frozen local cache. The nine geometry fields were bounding-box
X/Y/Z, volume, volume state/method/unavailable cause/not-calculated cause, and
triangle count. The source ledger stayed unchanged and each arm made zero R2
acquisition requests. Verification downloads and the offline image audit are
outside publication timing.

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

Both optimized arms measure CI-green source commit
`20d60f8b32546e310d5c24e347f4ebe10d214900`. The unused pre-merge containers were
replaced before either optimized run. The baseline remains the prior measured
`fd76e2de57f3f8ae1be4a2d1082c7ff901831bed` image. The old-versus-new comparison
therefore includes current main integration as well as codec scheduling, quality
and dimensions; the optimized GPU-versus-CPU comparison uses identical source.

Main integration preserves renderer provenance validation, exposes alpha-quality
metadata, keeps both historical visual fixtures and adds v3 for quality 85.
G-code thumbnail recipe becomes 3 because main already published recipe 2 at
quality 90. These measurement inputs are STL/OBJ/STEP only.

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
readback and GPU fence waits. Coordinator entry includes directory/open/checkpoint/
flock retry costs; it is not a measurement of the flock syscall alone. Lock hold
measures admission critical sections, excluding release. A 250 ms cgroup sampler
records RAM, CPU, I/O and memory pressure. Its per-process rows are unavailable
because `/proc/PID/io` is absent; missing readings are not zero. Application
supervisor telemetry separately records each child tree’s peak RSS. A one-second
physical adapter sampler records utilization and VRAM, including other Windows
users. No per-process dedicated VRAM reading is available.
Publication uses separate WSL monotonic and UTC clocks. No Windows process is
used to run the benchmark or collect a host performance counter.

## Measurement limits and execution-path evidence

The baseline rendered 967 frames on the GPU; the optimized run rendered 963.
Its first four inputs completed through CPU recovery after traced broker
connection/render-exchange errors. The broker completion count was zero after
that first wave. The remaining six non-GPU outputs in each arm are consistent with the known
large-STL envelope routes in the previous report. The broker’s zero fallback counter excludes client-side
connection failures; it must not be read as zero CPU recovery. No more specific
cold-start error reason was recorded by this tracer.

The new GPU run’s native pool admits parsers only. The old pool also admitted
encoders with one-byte scheduling tokens. Parser-only admission is reported
separately: removing codec waiting can expose more parser waiting without
proportionally reducing elapsed time. Codec byte tokens are not a RAM allowance;
frame/codec memory stays charged to the retained thumbnail host permit.

These are one pass per arm with cached sources and warm host filesystem caches.
The first-four measurement is cold pipeline work, excluding deployment/API boot.
Other existing deployments were preserved. Light repository work and the final
candidate image build occurred during the baseline; unrelated Windows activity
was not controlled. There was no concurrent benchmark or local test suite.
Tracing overhead is included and has no uninstrumented control. The resource
probe’s own sample duration is included in the aggregate evidence.

The `module_import` timer covers instrumented target modules only, not every
Python or native-library import. Mesh load includes lazy parser imports.
Stage spans are selected by start time inside the publication interval; active
concurrency is clipped to that interval. A long-lived permit whose release has
not occurred does not produce a completed lifetime span, so zero lifetime
occupancy for the broker’s inference-model permit is not zero retained ownership.
Completed parser, host and codec lifetimes supply their concurrency observations.
Container RAM includes page cache; supervisor RSS is a sampled child-tree peak.
Neither should be treated as the native reservation’s virtual-memory ceiling.

GPU queue submission is host-call time. Mapping includes the completion fence;
compact mapped staging writes are included in geometry encoding rather than a
separate `write_mapped` timer. No pure PCIe bandwidth or device kernel timestamp
is claimed. Socket-call duration includes blocking and readiness polling. Broker
connection setup includes cancellation checkpoints and runtime/profile validation,
not merely the Unix socket’s connect syscall. Separating those internal costs is
a follow-up profiling target.

## Behavior coverage

Tier paths below are relative to `backend/tests/`; `packages/` paths are relative
to `backend/`.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | encodes while a parser owns the native pool | Happy | GPU frame; full parser reservation | Image completes before parser release | Integration | ✅ `integration/modules/media/test_thumbnail_handoff.py::TestCodecProgress::test_encodes_while_a_parser_owns_the_native_pool` |
| 2 | bounds simultaneous codecs | Edge | Two prepared owners | Only one codec active | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_bounds_simultaneous_codecs` |
| 3 | requires host custody | Error | No host permit | Admission refused | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_requires_host_custody` |
| 4 | refuses native credit during codec admission | Error | Parser permit held | Reversed admission refused | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_refuses_native_credit_during_codec_admission` |
| 5 | refuses nested codec admission | Error | Codec already active | Recursive admission refused | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_refuses_nested_codec_admission` |
| 6 | releases codec credit after withdrawal | Error | Encoder raises cancellation | Next encoder enters | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_releases_codec_credit_after_withdrawal` |
| 7 | withdraws a waiting codec | Error | Another encoder owns slot | Cancelled waiter exits; subsequent admission succeeds | Integration | ✅ `integration/runtime/test_preparation_runtime.py::TestCodecCustody::test_withdraws_a_waiting_codec` |
| 8 | compresses preview pixels with bounded visible error | Happy | Spatially coherent detailed frame | Under 60% of lossless bytes; RGB mean absolute error below 5/255 | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_compresses_preview_pixels_with_bounded_visible_error` |
| 9 | preserves decoded alpha | Edge | Transparent and partial alpha | All alpha bytes exact | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_decoded_alpha` |
| 10 | preserves exact PNG pixels | Edge | RGBA frame | PNG round trip exact | Unit | ✅ `packages/printstash-core/tests/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_exact_png_pixels` |
| 11 | preserves generated framing | Edge | Generated WebP | Publication retains encoded bytes | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_preserves_generated_framing` |
| 12 | retains full geometry with smaller previews | Happy | Real STL; width 512 | Original bytes/triangle count retained; complete 512×384 lossy preview | E2E | ✅ `e2e/test_ingest.py::TestMetadata::test_retains_full_geometry_with_smaller_previews` |
| 13 | rederives thumbnails from the previous GPU recipe | Edge | Old ready/failed mesh thumbnail | Normal source finds stale recipe | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_rederives_thumbnails_from_the_previous_gpu_recipe` |
| 14 | keeps previous thumbnail visible during recipe backfill | Edge | Old published thumbnail | Existing pointer retained until replacement | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_keeps_previous_thumbnail_visible_during_recipe_backfill` |
| 15 | rederives G-code thumbnails from previous recipes | Edge | Old G-code thumbnail | Normal source finds stale recipe | Integration | ✅ `integration/modules/derivatives/test_source.py::TestPending::test_rederives_gcode_thumbnails_from_previous_recipes` |
| 16 | preserves frozen visual input hashes | Edge | Versioned tetrahedron fixture | Six analytic hashes unchanged; new thumbnail/recipe identity pinned | Integration | ✅ `integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes` |
| 17 | counts partial socket transfers | Edge | Short sends/receives | Actual bytes/calls measured | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestTransportTrace::test_counts_partial_socket_transfers` |
| 18 | preserves socket timeout | Error | Socket timeout | Exception retained; timeout counted | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestTransportTrace::test_preserves_socket_timeout` |
| 19 | emits transfer bytes without payload | Edge | Transfer containing private payload | Only size/timing emitted | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestTransportTrace::test_emits_transfer_bytes_without_payload` |
| 20 | observes real admission without changing custody | Edge | Actual file-lock resource pool | Permit retained; released capacity reusable | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestPoolTrace::test_observes_real_admission_without_changing_custody` |
| 21 | preserves cancellation during admission | Error | Withdrawn claim | No admission; original exception preserved | Integration | ✅ `integration/scripts/test_compute_pipeline_trace.py::TestPoolTrace::test_preserves_cancellation_during_admission` |
| 22 | rejects invalid preview recipes | Error | Invalid quality/method, nonboolean encoding flags, malformed shape | Typed validation raises | Unit | ✅ `packages/printstash-core/tests/mesh/test_preview_profile.py::TestPreviewProfile::test_profile_structure_rejects_invalid_recipe` |
| 23 | encodes generated PNG without reframing | Happy | Valid generated PNG | Canonical WebP with exact source alpha | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_encodes_generated_png_without_reframing` |
| 24 | rejects invalid generated frames | Error | Empty, wrong dimensions, absent alpha, edge-touching frame | Typed frame validation raises | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_invalid_generated_frames` |
| 25 | rejects malformed generated bytes | Error | Undecodable image | Validation raises | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_malformed_generated_bytes` |
| 26 | rejects oversized generated declarations | Error | 5001×5000 PNG declaration | Refused before image allocation | Unit | ✅ `unit/modules/media/test_thumbnail.py::TestGeneratedFrame::test_rejects_oversized_generated_declarations` |
| 27 | shares the preview recipe with the frontend | Edge | Canonical manifest | Generated TypeScript exactly matches manifest | Unit | ✅ `packages/printstash-core/tests/mesh/test_preview_profile.py::TestPreviewProfile::test_generated_frontend_profile_matches_manifest` |

All 17 CI checks, including PR gate, passed for the measured runtime/test source
`20d60f8b` ([CI run](https://github.com/xiao-villamor/PrintStash/actions/runs/38184924956)).
The completed matrix has 27 covered behaviors and no missing rows.

Local validation: 330 core/search tests; 116 ingestion/derivative/custody tests;
160 affected media/custody/tracer tests. Selections overlap and are not summed.
The broader media run was 2352 passed/9 failed; four failures were obsolete
lossless assertions, five observed different profile bytes across parent/child
while the profile was being edited. All affected cases passed in the fixed-source
160-test rerun. Ruff and the scoped core production format check pass.

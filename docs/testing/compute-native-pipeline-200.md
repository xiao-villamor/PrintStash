# Native GPU pipeline audit: 200 cached models

The same cohort contains **84 STL, 83 OBJ and 33 STEP** sources. Selection SHA256:
`077e93816fab660a38764f72a356bcda906bb5fea45118567b77af31ef7672a8`.
Every run uses a fresh deployment with the same hardware, image dependency stack,
concurrency and request policy. The first four sources include cold broker startup;
the remaining 196 form a different subcohort, so their timings are not a cold/warm
speedup comparison. Order is fixed (STEP, then STL, then OBJ); per-format latencies
also reflect queue position. Historical CPU ingestion/rendering was not repeated.

All inputs come from the immutable local cache. Docker uses an internal network;
no new R2 request is permitted. Every completed arm verified all 200 originals,
metadata and thumbnails, with exact authoritative geometry matching earlier output.
The request-ledger digest is unchanged. Images are for user review; exact identity
with the old CPU raster is no longer a requirement for this rendering recipe.

## Measurements

| Arm | Windows QPC | WSL UTC | WSL monotonic | Broker execution | Native admissions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous GPU (`b898824f`) | 472.416 s | 471.945 s | 447.574 s | 14.552 s | 200 |
| Unconditional handoff (`3988d697`) | 607.040 s | 606.927 s | 575.164 s | 7.283 s | 399 |
| Selective handoff (`5814e6e7`) | 470.710 s | 470.906 s | 445.148 s | 7.288 s | 254 |

QPC observations bracket upload through durable publication and include boundary
invocation gaps. UTC and WSL monotonic diverged, so every clock domain and wrapper
interval is retained in [the aggregate evidence](compute-native-pipeline-200.json).
No clock was changed and no inferred correction applied. GPU device execution
remains entirely inside Docker/WSL; Windows only supplies independent clock samples.

Broker execution includes host work and synchronization, not GPU-busy duration.
Transfer metrics overlap execution. Reserved-byte snapshots are not physical VRAM
peaks. Native phase/supervision durations overlap between processes and cannot be
added to obtain wall time.

## What the first candidate established

The compact native renderer approximately halved broker execution, removed the
legacy whole-model normal retries, and reduced the final device reservation
snapshot from about 255 MB to 171 MB. These did not produce an end-to-end gain:
the first candidate was about **28.5% slower** across all recorded clock domains.

There were 199 parser-to-encoder handoffs. Their finalizer supervision accumulated
368.097 seconds (median 1.840 seconds) and finalizer admission waits accumulated
193.346 seconds (p95 5.799 seconds). These are overlapping elapsed durations, not
isolated codec CPU time. Splitting every model doubled native process admission
and restricted prepared work to the three maximal packets fitting the 192 MiB
staging sublimit. Images could also be normalized and encoded a second time by the
publisher after the GPU had completed them.

The intermediate correction staged only a request whose existing conservative memory estimate
claimed the entire native pool. It reused that exact preflight plan in the
supervisor. Small models kept one parser/render/encoder lifecycle. The standalone
encoder avoids configuration and parser imports, and validated full-render output
retains its generated frame and existing WebP bytes. External embedded previews
retain their normalization. RGBA assembly also removes redundant complete copies.
No capacity estimate or process containment was relaxed.

The selective-handoff run restored the previous end-to-end duration but did **not**
establish a gain: its approximately 0.5% monotonic difference is smaller than the
five-second final polling interval. QPC and UTC likewise do not establish a useful
improvement from one observation. All 200 originals and authoritative measurements
matched; all saved frames contained alpha ≥128 foreground.

The remaining 54 handoffs accumulated 76.483 seconds of finalizer supervision
(median 1.412 seconds) and 147.526 seconds of readmission wait. Combined parser and
finalizer supervision was 823.510 seconds, versus the old GPU version’s 800.256
seconds; these are overlapping durations. Thirty-three handoffs were STEP, with a
median prepared packet of only 2,401 bytes. A whole-pool reservation expresses
uncertainty about parser memory, not enough GPU work to justify a second process.

The finalizer-based handoff was removed at `d8f64ccc`, retaining the compact GPU
renderer, lossless codec, publication validation, CPU recovery and conservative
parser reservations. Both arms were then measured with up to 40 pending Artifacts
and the same 200 sources and order. This comparison is separate from the
four-upload-wave measurements above.

| Bounded continuous40 arm | Windows QPC | WSL UTC | WSL monotonic | Broker execution | Native admissions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous GPU (`b898824f` equivalent source) | 473.029 s | 472.920 s | 446.924 s | 13.943 s | 200 |
| Combined worker (`d8f64ccc`) | 496.544 s | 497.070 s | 469.685 s | 7.056 s | 200 |

The combined worker still **regressed about 5%** in complete publication despite
approximately halving broker execution. All 200 sources, exact measurements and
requested outputs were retained; every saved 640×480 image had alpha ≥128
foreground. No improvement claim is made from the isolated broker gain. The last input was
accepted 22.209 seconds later, while the final drain differed by only 0.552 seconds.
Staging refusals increased from one to three, adding two accumulated seconds of
backoff, insufficient to assign the full difference to that cause. Load phases
increased from 269.370 to 281.715 accumulated seconds; native waits from 559.433 to
597.581. These overlap and do not isolate import, parser, codec or scheduling
causality on the shared WSL host.

The reference executed 221 singleton render batches: 199 requests minus four
image-cache hits plus 26 normal-preparation retries. The combined worker executed
191 singleton batches and two batches of two models, for 195 frames, with four
cache hits and no normal retry. Real cross-model batching therefore occurred but
was uncommon; a window of 40 pending Artifacts is not 40 prepared GPU inputs.
Fifty-five reference parser admissions claimed the entire native memory pool.
The unchanged FIFO admission and synchronous worker lifetime prevent another
parser from running during such a claim. The coalescer only gathers compatible,
already-ready requests for up to 10 ms.

The next correction separates prepared geometry custody from parser lifetime
without starting a second encoder process on GPU success. The existing Job will
retain bounded packet/pixel/codec reservations, release parser resources only
after clean reap, and encode validated final GPU pixels in its own execution
thread. An isolated CPU fallback remains necessary for GPU refusal. This route
has passed its correctness checks. At the operator’s request, final deployment
validation processed all 1000 cached models instead of repeating this
200-source candidate arm. The [complete result](compute-final-gpu-1000.md) preserves
973 ready outputs and the same 27 invalid sources, but does not establish a
throughput improvement. The frozen 200-source reference and earlier candidates
remain separate evidence.

The initial continuous harness was aborted after the cold wave when it exceeded
the existing four-active-ingestions-per-user staging limit and received HTTP 507.
Free disk was not the constraint. That partial attempt is invalid as a 200-model
measurement and is retained separately. A second partial attempt bounded uploading plus nonterminal ingestion Jobs to
four but exposed the short interval between Job completion and staging-lease
release; it was also aborted and excluded. The final harness keeps that bound
and at most 40 pending requested outputs, and retries only HTTP 507 with exact
`staging_capacity_exceeded` detail. That refusal rolls the request transaction
back before commit. Retries reopen the local source, wait one second, and are
bounded by 30 seconds per input and the original run deadline. Other failures
abort without replay. Seven offline HTTP/clock checks cover this policy. Both
arms use the same harness and include these waits in the publication interval;
application quotas are unchanged.

## Coverage and remaining scopes

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Complete same mixed cohort | Happy | Same 200 local sources | 200 metadata + 200 thumbnails READY | Deployment | ✅ All completed arms, including combined continuous40 |
| Preserve originals and measurements | Invariant | Cached bytes and prior metadata | 200 hashes/sizes and exact geometry agree | Deployment | ✅ All completed arms |
| Avoid storage requests | Resource | Internal network and local cache | Zero R2; unchanged request ledger | Deployment | ✅ |
| Retain failed optimization evidence | Error | Unconditional handoff | Regression documented rather than reported as speedup | Measurement | ✅ |
| Reduce full pipeline duration | Performance | Matched ready-geometry run | Source-to-publication improvement measured separately from broker speed | Deployment | ❌ Combined run regressed ~5%; complete 1000-source follow-up did not establish improvement |
| Review new visual recipe | Visual | 200 paired output images | User acceptance | Manual | Pending user review |
| Maintain interactive latency under load | Performance | Concurrent interactive queries | p95 gate established | Deployment | Not measured; no auto-qualification receipt |
| Qualify other vendors/deployments | Portability | AMD/Intel/other driver stacks | Real work on intended adapters | Deployment | Not measured; support not claimed |

[Visual/transport checks](compute-visual-preparation.md),
[handoff/publication checks](compute-thumbnail-handoff.md) and
[lossless encoding checks](compute-encoding.md) retain their exact test nodes.

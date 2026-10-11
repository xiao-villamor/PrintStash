# Native GPU pipeline audit: 200 cached models

The same cohort contains **84 STL, 83 OBJ and 33 STEP** sources. Selection SHA256:
`077e93816fab660a38764f72a356bcda906bb5fea45118567b77af31ef7672a8`.
Every run uses a fresh deployment with the same hardware, image dependency stack,
concurrency and request policy. The first four sources include cold broker startup;
the remaining 196 form a different subcohort, so their timings are not a cold/warm
speedup comparison. Order is fixed (STEP, then STL, then OBJ); per-format latencies
also reflect queue position. Historical CPU ingestion/rendering was not repeated.

All inputs come from the immutable local cache. Docker uses an internal network;
no new R2 request is permitted. Both completed arms verified all 200 originals,
metadata and thumbnails, with exact authoritative geometry matching earlier output.
The request-ledger digest is unchanged. Images are for user review; exact identity
with the old CPU raster is no longer a requirement for this rendering recipe.

## Measurements

| Arm | Windows QPC | WSL UTC | WSL monotonic | Broker execution | Native admissions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous GPU (`b898824f`) | 472.416 s | 471.945 s | 447.574 s | 14.552 s | 200 |
| Unconditional handoff (`3988d697`) | 607.040 s | 606.927 s | 575.164 s | 7.283 s | 399 |
| Corrected selective handoff | Pending | Pending | Pending | Pending | Pending |

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

The correction stages only a request whose existing conservative memory estimate
claims the entire native pool. It reuses that exact preflight plan in the
supervisor. Small models keep one parser/render/encoder lifecycle. The standalone
encoder avoids configuration and parser imports, and validated full-render output
retains its generated frame and existing WebP bytes. External embedded previews
retain their normalization. RGBA assembly also removes redundant complete copies.
No capacity estimate or process containment was relaxed to make this faster.

## Coverage and remaining scopes

| Behaviour | Category | Input | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| Complete same mixed cohort | Happy | Same 200 local sources | 200 metadata + 200 thumbnails READY | Deployment | ✅ Base and first candidate; corrected run pending |
| Preserve originals and measurements | Invariant | Cached bytes and prior metadata | 200 hashes/sizes and exact geometry agree | Deployment | ✅ Base and first candidate |
| Avoid storage requests | Resource | Internal network and local cache | Zero R2; unchanged request ledger | Deployment | ✅ |
| Retain failed optimization evidence | Error | Unconditional handoff | Regression documented rather than reported as speedup | Measurement | ✅ |
| Reduce full pipeline duration | Performance | Matched corrected run | Source-to-publication improvement measured separately from broker speed | Deployment | Pending |
| Review new visual recipe | Visual | 200 paired output images | User acceptance | Manual | Pending user review |
| Maintain interactive latency under load | Performance | Concurrent interactive queries | p95 gate established | Deployment | Not measured; no auto-qualification receipt |
| Qualify other vendors/deployments | Portability | AMD/Intel/other driver stacks | Real work on intended adapters | Deployment | Not measured; support not claimed |

[Visual/transport checks](compute-visual-preparation.md),
[handoff/publication checks](compute-thumbnail-handoff.md) and
[lossless encoding checks](compute-encoding.md) retain their exact test nodes.

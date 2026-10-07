# M6 production startup comparison

Measured integrated commit: `48bc1eee`. Baseline: `710e4eb7`. This qualifies
the M6 comparison corpus, not final M11 delivery or performance on every device.

## Protocol and provenance

200 completed samples plus four separately retained warm-ups: distributed root and
dense90-Model collection, EN/ES,30 warm and20 fresh contexts per combination.
Real SQLite and production nginx; Chromium148.0.7778.96,1440×900, active service
worker, Node24.19.0. Exact observer, lockfiles, seed, proxy/config and launcher
hashes match M0. No retries, discarded outliers or overlapping test suites.
The same host had lower initial load than M0 (0.61/1.27/2.65 versus approximately
2.3/2.4/2.3). This is a shared-host before/after observation, not randomized causal
attribution to one frontend change. Both backend contracts and frontend ownership
changed between these commits.

Preserved M7 upload-workflow files were dirty and their hashes were recorded in
the run manifest. These deferred forms were not opened by the timing observer.
The subsequent M6 selection correction changes only an interaction handler,
not a startup or asset path; its correctness is qualified separately. Production
sources did not change during either timing run. The dense invocation passed2 cases in4.2 minutes;
distributed invocation passed2 cases in3.8 minutes. Both raw logs are retained.

## Readiness (milliseconds)

Ready is the unchanged two-frame DOM content/tree observer. Fresh context means
new authenticated browser storage with a warm OS/database, not a cold server.

| Corpus | Locale | Cache | n | Before median / p95 | Current median / p95 |
|---|---|---|---:|---:|---:|
| dense | en | warm | 30 | 451.85 / 636.00 | 394.75 / 489.10 |
| dense | en | fresh context | 20 | 788.00 / 1247.20 | 730.65 / 1012.30 |
| dense | es | warm | 30 | 429.40 / 854.90 | 384.25 / 456.40 |
| dense | es | fresh context | 20 | 702.00 / 1158.30 | 702.20 / 731.50 |
| distributed | en | warm | 30 | 283.90 / 356.70 | 253.75 / 313.70 |
| distributed | en | fresh context | 20 | 573.45 / 1636.60 | 539.70 / 586.50 |
| distributed | es | warm | 30 | 277.65 / 663.40 | 254.00 / 338.30 |
| distributed | es | fresh context | 20 | 559.70 / 592.90 | 532.20 / 586.00 |

Every measured current warm median is below500ms, warm p95 below800ms and fresh
median below1000ms. Dense Spanish fresh median is effectively unchanged
(702.0→702.2ms), rather than an improvement. M0 dense Spanish warm p95 missed
800ms; current456.4ms clears that reference. Budgets were assessed after collecting
all samples, not used to censor slow observations.

## Decoded visible images (milliseconds)

Each dense sample verified six connected images with unchanged sources, successful
`decode()` and nonzero natural dimensions. This is an observer upper bound after
polling and a frame, not exact paint time. Distributed root has zero images and is
explicitly not applicable.

| Locale | Cache | n | Before median / p95 | Current median / p95 |
|---|---|---:|---:|---:|
| en | warm | 30 | 1565.55 / 2376.20 | 782.15 / 902.30 |
| en | fresh context | 20 | 1963.30 / 2468.90 | 1083.30 / 1347.40 |
| es | warm | 30 | 1535.15 / 1972.70 | 770.85 / 1006.40 |
| es | fresh context | 20 | 1852.10 / 2305.80 | 1063.50 / 1155.20 |

## Contention and tradeoffs

At the later readiness-observation checkpoint, dense median admitted requests fall
from87 to54–54.5. Median maximum simultaneous work over the whole dense sample
falls from31 to12 for warm contexts and31–31.5 to24 for fresh contexts. These
counts include scripts, API and the subsequent folder interaction; they are not
image-concurrency statistics. The separate held-response browser test asserts the
actual four-download protected-image limit.

Completed non-thumbnail API requests observed before image verification have
dense median durations of91.8–111.0ms versus205.65–273.55ms; their p95 values
are228.0–248.4ms versus734.2–961.0ms. Endpoint mixes differ, and aborted requests
are absent from Resource Timing, so this is contention context, not a backend
microbenchmark or queue-delay measurement. Completed thumbnail request medians
are115.35–133.55ms versus429.7–526.6ms. Median completed API transfer bytes per
dense sample fall from33,493–36,335 to31,042–31,462. These include reported HTTP
overhead; cached resource transfer sizes can be zero. Raw server-timing records
retain app/SQL durations and statement counts.

Distributed median request counts rise37→40. The median fetch count remains14;
the script count rises19→22 after module splitting. Median completed API transfer
bytes rise19,664→20,022. Current root readiness improves in this observation,
but extra module requests are a recorded cost, not a performance improvement.
No additional bundling layer is justified by this small corpus; final M10/M11
platform qualification must retain visibility of the cost.

All200 samples have zero observed HTTP statuses≥400. There are211 failed-request
notifications versus197 in M0:197 dense plus14 distributed Jobs requests. The14
new notifications occur during the first31ms of warm reloads, around the outgoing
document transition, before new application readiness; six started before the new
document request. That chronology is consistent with cancellation, but the unchanged
observer does not capture failure reasons, so these are not classified as confirmed
application failures or silently removed.

## Budget decision and limits

Retain four concurrent protected downloads and the200px admission margin: the
current corpus meets readiness references, visible decoding improves, and the
functional tests prove bounded admission/cancellation. No claim that four is a
globally optimal value is made. Keep idle storage bounded by400 entries and32MiB
of encoded Blob bytes. Mounted leases are excluded from idle eviction. This is
not a bound or measurement of decoded image/GPU memory.

Correctness is reported in [M6 validation](frontend-m6-validation.md), separately
from these timings. Deep remembered trees, mobile, restricted identities and
ingest-load performance are not measured here. Neither JS execution/render CPU,
browser scheduling queue time nor decoded-memory retention is inferred from
request count, resource bytes or test success. These remain explicit M11 evidence
limits; final integrated measurements must use the final implementation SHA.

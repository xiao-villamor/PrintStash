# Integrated startup observation (M11 checkpoint)

Measured production source: `371d781a`, after M10. Baseline: `710e4eb7`.
The subsequently merged `origin/main` had exactly the same tree as the baseline;
its history-only reconciliation changed no production bytes. New Spoolman tests
and review notes were written while collecting samples; production sources did
not change during the runs. This checkpoint predates the final Spoolman correction
and does not claim final-SHA qualification.

The unchanged observer measured 200 completed observations plus four retained
warmups: distributed root/dense 90-Model collection, EN/ES, 30 warm and 20 fresh
authenticated contexts each, production nginx, real SQLite, active service worker,
1440×900 Chromium. No other local suites overlapped the observations. Fresh context
is not a cold database or OS. Protocol and baseline limits remain those in
[baseline qualification](frontend-architecture/baseline-measurement.md).

## Readiness

Milliseconds, median / nearest-rank p95. All observations are retained, including
outliers. This shared-host comparison is not randomized causal attribution.

| Corpus | Locale | Cache | n | Baseline | Integrated |
|---|---|---|---:|---:|---:|
| dense | en | warm | 30 | 451.85 / 636.00 | 392.85 / 761.10 |
| dense | en | fresh context | 20 | 788.00 / 1247.20 | 704.50 / 788.00 |
| dense | es | warm | 30 | 429.40 / 854.90 | 400.30 / 470.50 |
| dense | es | fresh context | 20 | 702.00 / 1158.30 | 718.30 / 830.40 |
| distributed | en | warm | 30 | 283.90 / 356.70 | 260.90 / 401.20 |
| distributed | en | fresh context | 20 | 573.45 / 1636.60 | 533.50 / 613.00 |
| distributed | es | warm | 30 | 277.65 / 663.40 | 257.85 / 336.50 |
| distributed | es | fresh context | 20 | 559.70 / 592.90 | 538.15 / 610.90 |

All warm medians remain below 500ms, warm p95 below 800ms and fresh medians below
1000ms. Dense English warm p95 regresses 636→761.1ms; distributed English warm
p95 regresses 356.7→401.2ms; dense Spanish fresh median rises 702→718.3ms and
Spanish distributed fresh p95 rises 592.9→610.9ms. These are retained costs, not
waived or described as universal improvement. Median readiness improves in seven
of eight groups. The slowest dense English warm sample (867.2ms) reports browse app time 688.2ms versus SQL time 39.1ms over 19 statements; outliner app time 120.3ms versus SQL time 9.1ms over eight statements. These server timings show that the tail includes substantial non-SQL server time. They cannot assign that time to a specific CPU or scheduler cause, or establish a frontend regression from readiness alone.

## Decoded visible thumbnails

Every dense sample decodes six visible images and verifies stable attached sources.
The timestamp is an upper bound after polling, decode and a frame, not exact paint.
Distributed root contains no visible Model thumbnails (not applicable).

| Locale | Cache | n | Baseline | Integrated |
|---|---|---:|---:|---:|
| en | warm | 30 | 1565.55 / 2376.20 | 777.05 / 1233.30 |
| en | cold | 20 | 1963.30 / 2468.90 | 1071.10 / 1205.10 |
| es | warm | 30 | 1535.15 / 1972.70 | 781.10 / 1162.60 |
| es | cold | 20 | 1852.10 / 2305.80 | 1110.55 / 1286.60 |

## Network observations

| Corpus | Locale | Cache | Median requests at ready | Median maximum in flight | HTTP ≥400 | Failed-request notifications |
|---|---|---|---:|---:|---:|---:|
| dense | en | warm | 55.0 | 12.0 | 0 | 59 |
| dense | en | cold | 54.0 | 24.0 | 0 | 40 |
| dense | es | warm | 54.0 | 12.0 | 0 | 60 |
| dense | es | cold | 54.0 | 24.0 | 0 | 40 |
| distributed | en | warm | 40.0 | 7.0 | 0 | 4 |
| distributed | en | cold | 40.0 | 24.0 | 0 | 0 |
| distributed | es | warm | 40.5 | 7.0 | 0 | 5 |
| distributed | es | cold | 40.0 | 24.0 | 0 | 0 |

Counts include scripts, API and the subsequent successful folder interaction.
Failed-request notifications are not classified as network failures: the observer
does not record abort reasons. No inference about browser scheduling queue time,
render CPU, JS execution time or decoded/GPU memory comes from these counts.
Correctness and module ownership are evaluated separately in the milestone matrices.

## Supplemental resource observation contract

The comparable readiness observer remains unchanged. A separate instrumented run
records Chromium script/task/layout time, React root commits, encoded Blob URL
retention and resource transfer/response timings after dense content is usable.
It records the first page and all 90 Models, then observes Blob retirement on real
sign-out. Instrumentation overhead makes these diagnostic values ineligible for
the readiness comparison. No baseline CPU/render/Blob numbers were collected;
these measurements cannot establish an improvement for those dimensions.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| P1 | records resources for a usable dense library | Happy | production build and real seeded API | decoded visible images; CPU/commit counters populated; all 90 Models reachable | Playwright measurement | ✅ `tests/performance/startup-resources.spec.ts` — 1 passed; both observations asserted |
| P2 | retires measured Blob URLs on sign-out | Edge | mounted private thumbnails then real sign-out | zero retained encoded Blob URL bytes | Playwright measurement | ✅ `tests/performance/startup-resources.spec.ts` — 1 passed; both observations asserted |

### Instrumented result on the reviewed application source

Application revision `66ba9036`; corrected diagnostic test executed locally once
successfully (8.5s test body, 1.6min including production setup). This separate run
overlapped functional test work; its timings are diagnostic, not a comparable
performance benchmark. The dense observer runs only against the dense corpus.

| Observation | First page (24 Models) | Expanded (90 Models) | Signed out |
|---|---:|---:|---:|
| React root commits since document start | 26 | 123 | 129 |
| Chromium cumulative script time (s) | 0.634 | 1.409 | 1.501 |
| Chromium cumulative task time (s) | 1.167 | 3.371 | 3.690 |
| JS heap used (bytes, no forced GC) | 10,121,132 | 19,814,308 | 23,196,872 |
| Retained encoded Blob URLs | 4 | 33 | 0 |
| Retained encoded Blob bytes | 482 | 4,012 | 0 |

The unchanged document time origin confirms that logout retired the Blob URLs
without hiding their lifetime behind a document reload. Heap size was not forced
to collect and does not demonstrate a leak or memory reclamation. These tiny seed
thumbnails do not model decoded-image/GPU memory for real collections. Root commit
counts are not per-component render costs. Resource timing records request-start
and response-wait intervals, but does not separate all browser scheduling causes.
No before/after CPU, render-count or memory improvement is claimed.

### CI application-source qualification

Deep CI run `37653862125` also retained 200 successful readiness samples plus
four warmups on application source `66ba9036`. Its two startup jobs failed later
in the separate resource observer (logout locator in dense; dense-only count
assertion applied to the distributed corpus). Those diagnostic-test corrections
do not change the comparable observer or application. These CI-host numbers are
not compared to the local baseline.

| Corpus | Locale | Context | n | Ready median / p95 (ms) |
|---|---|---|---:|---:|
| dense | en | fresh | 20 | 338.30 / 438.40 |
| dense | en | warm | 30 | 180.35 / 359.20 |
| dense | es | fresh | 20 | 326.95 / 344.00 |
| dense | es | warm | 30 | 188.80 / 220.20 |
| distributed | en | fresh | 20 | 385.85 / 408.60 |
| distributed | en | warm | 30 | 185.50 / 210.90 |
| distributed | es | fresh | 20 | 379.95 / 397.10 |
| distributed | es | warm | 30 | 189.55 / 223.50 |

## Later comparable observation: `90ecfbc0`

The same production observer collected another 200 completed observations plus
four retained warmups, sequentially, without overlapping local tests/builds.
Dirty-state changes during collection were test/document edits; production source
was held at `90ecfbc0`. Chromium 148.0.7778.96, Node 24.19.0, production nginx,
real SQLite corpus, active service worker and the original viewport/sample counts
were retained. This precedes the subsequent Materials/cache/maintenance changes;
it is **not a measurement of the final commit**.

| Corpus | Locale | Cache | n | Readiness median / p95 (ms) |
|---|---|---|---:|---:|
| dense | en | warm | 30 | 421.50 / 1025.70 |
| dense | en | fresh context | 20 | 765.05 / 1087.50 |
| dense | es | warm | 30 | 415.50 / 472.40 |
| dense | es | fresh context | 20 | 750.30 / 866.00 |
| distributed | en | warm | 30 | 278.50 / 347.70 |
| distributed | en | fresh context | 20 | 561.35 / 590.30 |
| distributed | es | warm | 30 | 262.75 / 353.90 |
| distributed | es | fresh context | 20 | 543.75 / 610.80 |

Dense English warm p95 exceeds the 800ms reference and regresses from baseline
636ms to 1025.70ms. Dense Spanish fresh median also regresses (702→750.30ms),
as does distributed Spanish fresh p95 (592.90→610.80ms). This result does not
support a universal latency improvement or a claim that every target was met.
All eight warm/fresh medians remain below their 500/1000ms references. In the
slowest dense English warm observation (1122.30ms), browse did not start until
796.80ms; its server app/SQL times were 223.5/27.8ms. This localizes much of the
delay before the browse request, but does not establish its cause.

Six visible dense thumbnails decoded in every sample. Median/p95 upper-bound
observations were EN warm 848.75/1385.50ms, EN fresh 1198.45/1583.20ms, ES warm
819.25/917.10ms, ES fresh 1148.65/1292.40ms. These remain below their corresponding
baseline values. Distributed root has no visible Model thumbnails; its observer
timestamps are not thumbnail measurements. Correctness, ownership and these
performance observations remain separate claims.

## Final comparable observation: `30a529d5`

The final broad integration checkpoint (including Materials, artifact cache and
maintenance ownership) retained 200 completed samples plus four warmups. Both
corpora ran sequentially without other local suites or builds. Production source
was fixed at `30a529d5`; dirty files during collection were review documentation
and new regression tests. Environment and protocol match the local baseline:
Chromium 148.0.7778.96, Node 24.19.0, nginx, real SQLite, active service worker,
1440×900, 30 warm and 20 fresh authenticated contexts per locale/corpus.

| Corpus | Locale | Context | n | Baseline median / p95 (ms) | Final checkpoint median / p95 (ms) |
|---|---|---|---:|---:|---:|
| dense | en | warm | 30 | 451.85 / 636.00 | 503.95 / 1055.90 |
| dense | en | fresh | 20 | 788.00 / 1247.20 | 864.15 / 1346.80 |
| dense | es | warm | 30 | 429.40 / 854.90 | 487.95 / 670.90 |
| dense | es | fresh | 20 | 702.00 / 1158.30 | 856.50 / 1009.30 |
| distributed | en | warm | 30 | 283.90 / 356.70 | 316.10 / 382.80 |
| distributed | en | fresh | 20 | 573.45 / 1636.60 | 651.10 / 766.20 |
| distributed | es | warm | 30 | 277.65 / 663.40 | 303.35 / 452.70 |
| distributed | es | fresh | 20 | 559.70 / 592.90 | 561.35 / 609.50 |

Every readiness median regresses in this checkpoint. Dense EN warm median/p95
exceed the 500/800ms references; all fresh medians remain below 1000ms. The
comparison therefore **does not establish faster startup or fulfillment of every
latency target**. These costs remain visible alongside correctness improvements.
The slowest dense EN warm sample is 1255.60ms: session validation occurs at
811.30ms and browse starts at 856ms, then takes 307.50ms (server app 299.30ms,
SQL 45.70ms / 19 statements). Much of the delay precedes browse. These phase
measurements do not identify the cause or justify attributing it to host load.

| Dense locale | Context | n | Decoded visible thumbnails median / p95 (ms) |
|---|---|---:|---:|
| en | warm | 30 | 971.55 / 1651.90 |
| en | fresh | 20 | 1332.90 / 1744.00 |
| es | warm | 30 | 928.85 / 1216.10 |
| es | fresh | 20 | 1320.55 / 1400.10 |

All dense observations decoded six visible images with stable attached sources.
These upper bounds improve on the baseline thumbnail observations in all four
groups. Distributed root has zero visible Model thumbnails and supplies no
thumbnail-decoding result. CPU, renders and memory retain only the separately
qualified diagnostic observations above; no before/after improvement is claimed.

Subsequent integration corrections fence SendToButtons session continuations and
move configuration snapshot reading out of the atomic-claims module to remove a
dependency cycle. They do not change library startup, browse, thumbnails, route
composition or its comparable observer. The measurements identify their actual
source checkpoint rather than claiming to have run on a later commit.

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
of eight groups. The final review must assess these tails against request evidence.

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

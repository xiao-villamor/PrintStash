# Cached 1000-model pipeline audit

## Scope and reproducibility

The corrected rendering pipeline was deployed from `26b8f4b4f5964a16fd879285648f334bf4a72e84`
and exercised wholly inside Docker/WSL on the physical RTX 5060. Native wgpu used
OpenGL through Mesa 25.0.7 / D3D12; no vendor compute API is required by the
implementation. This is evidence for this deployment, not other adapters/drivers.
The container used an internal Docker network with no external route.

All three arms use the same 1000 cached source identities and sizes: 481 STL,
486 OBJ and 33 STEP, totalling 2,424,942,340 bytes. Per operator instruction, only
the corrected GPU arm was executed again. Historical CPU and GPU arms from
`eb73b3bc12601674e0104681ed8e939b26c54e71` are retained. Thus the ratios include
CPU preparation, dispatch, admission and encoder changes as well as GPU work;
they do not isolate GPU speed or the effect of an individual optimization.

Each run used a four-upload submission window. The corrected deployment retained
four native slots, eight prepared slots, four source-I/O slots, a 3955 MiB native
RAM pool and a 1 GiB device admission budget. AI was disabled. Rendering preview
was explicit; automatic qualification remains gated. Sources were read locally,
then uploaded through the real application API. Jobs, workers and publication
were real. The timer spans submission through terminal metadata and thumbnails;
it includes queueing, parsing, preparation, transfers, rendering, encoding and
publication, including the same rejected inputs. Original downloads and gallery
analysis happened after the timed interval.

The request ledger SHA-256 remained
`d8c0026f30334f13e3579851663d1e9b3b8ded1e9f74cee423ea02ee7af62ebe`.
There were **zero R2 requests** during this rerun or analysis. This is not a claim
about the earlier corpus acquisition or account-wide billing. Private source
names, credentials and mesh files are excluded from repository evidence.

## Complete pipeline results

| Arm | Monotonic elapsed | Relative to corrected GPU |
|---|---:|---:|
| Historical CPU | 4050.997 s (67 min 31 s) | 1.956x |
| Historical GPU | 3222.049 s (53 min 42 s) | 1.556x |
| Corrected GPU | 2070.994 s (34 min 31 s) | 1.000x |

The corrected run uses 48.88% less timed duration than the historical CPU arm and
35.72% less than the historical GPU arm. These are a single three-format corpus
comparison, not universal or per-format qualification thresholds.

The new run's UTC endpoints span 2169.074 s (36 min 9 s), **98.080 s more**
than the monotonic interval. The UTC hardware sample series contains 64 gaps of
2.346–2.713 s at recurring intervals; their excess over the nominal one-second
spacing totals 98.357 s. The code captures both endpoint clocks adjacently, so
this difference is not a separately timed verification phase. The underlying
clock cause is not established. Both observations are retained; no global offset
is subtracted and UTC is not silently relabelled as monotonic elapsed time.

The ratios above compare recorded monotonic durations. Their conversion to
external wall-clock speedup is unqualified because clock domains disagreed.
UTC-based per-Artifact percentiles and native occupancy reconstructed by
subtracting monotonic durations from UTC log timestamps are diagnostic only.
The historical database retains only 500 ingestion timestamps (250 STL, 236 OBJ,
14 STEP), whereas the new report freezes all 1000; missing historical timestamps
are not imputed. The gallery labels both the clock limitation and paired sample
counts. No per-format or interactive-latency qualification follows from these
percentiles.

Compute diagnostics deliberately warmed the broker before timed submission.
The corrected probe took 3.392 s; its reported broker cold start was 2.379 s.
Lazy geometry shader initialization remained inside the timed run. The owner,
contexts, shaders and pools persisted across models. This is not a cold-start
benchmark, and the fresh disposable parser cost must not be called GPU startup.

| Format | Sources | Ready metadata and thumbnails | Same invalid sources | Exact authoritative geometry |
|---|---:|---:|---:|---:|
| STL | 481 | 454 | 27 | 454 / 454 |
| OBJ | 486 | 486 | 0 | 486 / 486 |
| STEP | 33 | 33 | 0 | 33 / 33 |
| Total | 1000 | 973 | 27 | 973 / 973 |

All 1000 original files were downloaded and checksum-verified. Original bytes,
terminal outcomes and geometry-presence decisions match both historical arms.
All 973 published thumbnails were downloaded and decoded. The 27 invalid STL
sources are the identical baseline identities, not new regressions.

## Batching, residency and remaining bottlenecks

The corrected broker completed 967 GPU render requests and reported no
infrastructure fallback. Its exact batch histogram was
`[1042, 25, 1, 0, 0, 0, 0, 0]`: 25 two-model batches and one three-model batch,
with 1042 singleton batches. The 1095 frames include 128 numerical retries;
these are execution attempts, not 1095 distinct source models. The baseline
recorded 967 singleton batches. All corrected render requests were background
compute requests; no interactive workload was injected.

Real cross-model batching works, but the low frequency shows that preparation
rarely supplied several compatible ready models within the 10 ms window.
Admission does not parse extra sources merely to fill the GPU batch. Several
models may remain resident within budget, while active geometry is pinned.
There were no geometry or shaded-output cache hits in this single-view corpus;
cache benefits must not be inferred from the measured speedup. Persistent runtime
reuse still applies. Final device reservations were 207,802,060 bytes; this is a
final snapshot, not peak VRAM or process memory. Native worker-tree peak RSS was
924,352,512 bytes, separately from broker host/device accounting.

GPU packing recorded 307 uploads and CPU packing 788, including retries. Of
179 GPU normal attempts, 128 required canonical CPU normal recovery (71.5%).
Healthy batch members retained their results. This numerical guard preserves the
canonical behavior, but the retry frequency is a remaining efficiency problem;
it is not hidden within the zero infrastructure-fallback counter. Normal and
packing counters reported 17.132 s and 18.131 s respectively, with overlapping
scope that must not be added to complete-pipeline time.

| Recorded aggregate | Historical GPU | Corrected GPU | Scope |
|---|---:|---:|---|
| Whole-pool native claims | 641 | 183 | Of 1000 admissions; uncertain formats retain conservative claims |
| Native admission waits | 3832.265 s | 2741.952 s | Sum of overlapping waits |
| Load phase | 1159.162 s | 1351.863 s | Sum of child load phases, including imports/parsing |
| Exact measurement phase | 120.174 s | 118.788 s | Sum of child phases |
| Render phase | 240.585 s | 505.445 s | Sum including CPU-side preparation and encoding |
| Supervision | 2958.542 s | 3929.442 s | Sum of child supervision durations |
| Broker execution | 45.805 s | 65.931 s | Host-side owner elapsed, not hardware timestamp queries |
| Broker transfer | 10.065 s | 12.748 s | Instrumented transfer time; not independent of execution |

The faster overall pipeline does not imply every phase became faster. The
corrected renderer restores canonical lossless WebP; the old accelerated encoder
used Pillow's lossy default. Concurrency and CPU preparation also changed, so the
higher aggregate render/load costs cannot be assigned to one cause from this
run. Admission and dispatch let work overlap more effectively. The remaining
supervision time outside explicitly measured child phases totals 1950.920 s;
it includes unseparated startup/import/IPC/callback/publication/cleanup costs,
not a measured 1950.920 s of imports or GPU initialization.

One-second `nvidia-smi` samples during publication reported GPU utilization
median 0%, p95 1%, maximum 14% across 2066 samples. They cover the whole physical
adapter, including unrelated Windows work, and can miss short render bursts.
They are diagnostic, not per-process utilization or hardware qualification.
Together with low batch frequency and the much longer native pipeline, they
show why filling VRAM alone does not remove the preparation/admission bottleneck.
Neither cache residency nor high VRAM usage proves high GPU execution occupancy.

## Visual evidence and qualification limits

The operator owns visual approval. The new three-column gallery preserves both
historical outputs and corrected outputs for all 1000 source positions, with
explicit rejected-input rows. Against canonical CPU, 810 of 973 thumbnails pass
the exact foreground mask at alpha >=128 and maximum RGBA difference <=8 check:
366/454 STL, 414/486 OBJ and 30/33 STEP. Exact masks match 956/973; decoded RGBA
matches byte-for-byte for 263/973. These pixel checks do not prove protected-part
visibility or human acceptance. The full strict corpus gate is **not passed**,
and no automatic qualification receipt is issued.

The historical GPU comparison passed that numeric gate for 6/973. Encoder and
recipe changes explain why old/new GPU pixels should not be treated as identical
outputs. Exact measurements, volume, source files and geometry identity remain
unchanged. Thumbnail recipe 13 and the corresponding raster/visual/compute
recipes carry the rendering change; device selection alone does not version
outputs or embedding identities.

Mixed interactive-load p95, aged-backfill progress under GPU query load, actual
device OOM/loss recovery and representative AMD/Intel/native-Linux combinations
remain unqualified. Unit/integration fault injection remains separate evidence.
ONNX hardware inference is outside this rendering follow-up.

## Coverage matrix for this measurement

| # | Behaviour | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| C1000-1 | Completes the mixed cached corpus | Happy | Same 1000 STL/OBJ/STEP inputs through Docker API | 973 requested outputs ready; identical 27 invalid inputs | Manual E2E | ✅ |
| C1000-2 | Preserves source and geometry | Happy | Saved original and derivative snapshots from all arms | 1000 source hashes/sizes and 973 exact geometry values agree | Manual E2E | ✅ |
| C1000-3 | Avoids additional remote requests | Edge | Immutable cache, internal Docker network, saved ledger | Identical ledger digest and zero new R2 operations | Manual E2E | ✅ |
| C1000-4 | Executes actual cross-model batches | Happy | Independently admitted ready geometry on RTX 5060 | 25 two-input and one three-input readback batch | Manual E2E | ✅ |
| C1000-5 | Records numerical recovery separately | Error | 179 real GPU normal attempts | 128 canonical normal retries; healthy outputs survive | Manual E2E | ✅ |
| C1000-6 | Preserves the full numeric visual gate | Happy | 973 decoded CPU/corrected-GPU image pairs | All masks exact and RGBA difference <=8 | Manual integration | ❌ 810/973; no qualification receipt |
| C1000-7 | Obtains operator visual acceptance | Happy | Three-column local comparison gallery | Operator decision recorded | Manual E2E | ⏭️ Pending operator review |
| C1000-8 | Qualifies interactive latency | Edge | Concurrent ingestion and interactive search | Interactive p95 <=110% of CPU reference | Manual E2E | ⏭️ Not measured in this corpus run |

Implementation behavior, fault boundaries and test-suite results are recorded
in [the batching coverage matrix](compute-model-batching.md). The implementation
commit passed all 17 CI checks, including PR gate:
[CI run](https://github.com/xiao-villamor/PrintStash/actions/runs/38153612342).
[Machine-readable aggregate](compute-cached-1000.json) contains no source names
or original model data.

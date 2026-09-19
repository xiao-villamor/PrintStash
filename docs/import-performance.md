# Measuring ZIP import performance

Use the complete archive as the benchmark input. From `backend/`:

```bash
uv run python scripts/bench_import.py /path/library.zip --output before.json
# Run again after changing the importer.
uv run python scripts/bench_import.py /path/library.zip --output after.json --compare before.json
```

Each run starts a fresh local server with a migrated database and temporary file
storage. SQLite is the default. Add `--database postgres` to use a disposable
PostgreSQL database in the repository's test container (Docker required). The
benchmark never accepts an existing vault database URL. For release-image runs,
the host can provision an isolated PostgreSQL service on a private container
network, then pass its maintenance URL through an environment variable named by
`--postgres-admin-url-env`. This mode requires the `postgres` maintenance database,
rejects URL query overrides, and still creates/drops only a generated per-run
database. Never supply a production service. The benchmark container needs no
Docker socket or host networking in this mode. Keep its credential environment
file private and out of evidence; reports record only server kind/version. Both databases use the
same supported application bootstrap as packaged deployments. Protocol v5 records
the database backend/server version and requires them to match for comparisons;
Older reports cannot be used as v5 references. Parsed slicer metadata and per-tool
material requirements must also match; run-specific IDs and timestamps are
excluded. It uploads the ZIP through the API, selects every supported file and
records source completion, then waits for metadata, background previews, lexical
projection and optionally similarity runs. The script checks the final file count and
failure count. A comparison also requires matching archive hashes and imported
file hashes and sizes. Preview states and failure reasons must also match. A G-code source without an
embedded thumbnail has the application's `not_applicable` outcome: its exact
`no_embedded_thumbnail` reason is recorded and is not an enrichment failure.
That exception requires a matching G-code source identity; missing assets,
unknown errors, and mesh preview failures still reject the run. The cache root
is private to each temporary benchmark vault. New reports
record dimensions, volume and triangle counts and compare those values too.

`saved_seconds` ends when the source import completes; `total_seconds` includes
background work. `background_after_save_seconds` is the difference. Both phases
probe the library API every 250 ms and record median, p95 and maximum response
latency. This distinguishes earlier availability from actual responsiveness.

Server CPU/RSS fields cover the application process tree; the separate PostgreSQL
container is outside that scope. They must not be described as total system or
database resource costs. Queue qualification additionally requires database cost
measurements and controlled resource limits; ordinary harness runs alone do not
satisfy the [staged migration performance gate](rust-import-migration.md).

The timers include upload, acquisition and processing. The legacy
`extraction_seconds` field measures selection acceptance; extraction now happens
incrementally during processing. Setup, database migration,
server startup and hashing the input archive are excluded. JSON output records
the separate timings, progress samples, API polling latency, Artifact job
statuses, server CPU time and sampled server memory use. Memory sampling runs
every 100 ms throughout upload, extraction and processing. It can miss shorter
peaks. RSS sums the server and its live subprocesses, so shared mappings can be
counted more than once. On Linux, CPU totals include reaped subprocesses as well
as live descendants. CPU seconds measure work across cores; elapsed seconds
measure how long the import takes. Run comparisons on the same machine with the
same settings, without other benchmarks or test suites running.

Reports also record the preview recipe hash, total encoded preview bytes and
the SHA256 of each decoded RGBA image, including its dimensions and state.
Image decoding and optional `--export-previews /path/previews` happen after
the timed interval. Export names include source and encoded hashes so different
outputs from the same source remain distinguishable.

Comparisons require identical compressed preview bytes by default. When testing
a lossless encoder change, use `--preview-comparison pixels` with `--compare`.
That mode requires a reference containing decoded pixel hashes and rejects any
changed pixel, dimensions or preview state. Imported source files and geometry
must still match. It does not allow reduced resolution or lossy compression.

Add `--similarity` to both commands to enable similarity indexing. Source
completion still precedes geometry and previews. The total timer waits for
similarity runs as well. Comparisons also check final fingerprint states,
component counts and quantized geometry hashes; failed, pending or missing
mesh fingerprints invalidate a run. Initial job hints are recorded separately
and are not final indexing evidence. This option does not enable AI model downloads or
measure semantic embedding generation; use an explicitly configured persistent
vault for those workloads. Temporary benchmark storage is removed afterward.

Source commands have scheduling priority. Background work becomes eligible
after 60 seconds even with continuous intake. A running calculation retains its
permit until completion. Queued analysis survives a normal vault restart.

Use `--timeout 10800` for a three-hour limit when measuring a slow reference.
Small archives can identify regressions, but their timings do not establish
throughput or memory use for a 50 GB library.

## Ingestion redesign measurement (2026-09-15)

These measurements precede integration with the branch's newer required Rust
rendering engine. They describe the measured pipeline revision, not a fresh
benchmark of the combined branch.

The comparison used one synthetic ZIP containing four binary STL meshes, each
with 327,680 triangles (29,275,026 compressed bytes in total), on the same
four-CPU Linux host. Each run used fresh SQLite/local storage, the same Rust
extension binary, and protocol `job-and-library-poll-250ms-v2`. No test suite ran
concurrently. These results isolate the pipeline redesign; they do not measure
the Rust kernels against their previous implementation.

Values below are means of two complete runs per configuration. The navigation
columns average each run's median and p95; they are not pooled percentiles.

| Configuration | Source saved (s) | All work complete (s) | CPU (s) | Peak RSS (MiB) | Navigation median / p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous pipeline, one worker | 9.825 | 9.884 | 14.340 | 452.8 | 47.6 / 94.8 |
| Background pipeline, one enrichment unit | 3.196 | 11.396 | 15.785 | 479.2 | 67.1 / 286.4 |
| Previous automatic concurrency (two workers on this host) | 7.709 | 7.762 | 13.495 | 615.1 | 52.4 / 294.6 |
| Background pipeline, default admission | 3.177 | 10.662 | 15.725 | 466.2 | 69.6 / 185.8 |

Compared with the previous default, sources became available **58.8% earlier**
and sampled peak memory fell **24.2%**. Tail navigation latency improved in
these runs, while median navigation latency increased. Completing every output
took **37.4% longer** and used **16.5% more CPU**. The controlled single-worker
comparison similarly saved sources earlier, but its navigation latency, memory,
CPU and total completion time all increased. Offloading changes when work runs;
it does not remove that work or guarantee lower request latency.

All four comparisons required identical source hashes/sizes, measured geometry,
preview states and encoded preview bytes. These are small-sample observations,
not throughput guarantees. In particular, the previous default used parallel
prefetch while the new default bounds enrichment to one local unit; that change
contributes to the memory/throughput tradeoff. `--workers` records the legacy
prefetch setting and does not increase the new enrichment worker count.

### Full similarity workload

A separate single-pair comparison enabled fingerprinting and candidate
verification on four small, differently scaled/deformed 80-triangle meshes
(4,282-byte ZIP). It compared the previous pipeline with the redesigned pipeline
**including the new Rust proximity kernel and plain analysis arrays**. Both completed all runs and
produced the same eight fingerprint records, source/geometry catalog and encoded
previews.

| Pipeline | Source saved (s) | All work complete (s) | CPU (s) | Peak RSS (MiB) | Navigation median / p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous | 3.943 | 153.552 | 203.170 | 415.9 | 44.2 / 185.4 |
| Background with native proximity | 1.334 | 44.299 | 128.160 | 426.3 | 98.9 / 333.3 |

Full completion was **3.47 times faster** and CPU work fell **36.9%**. Memory and
navigation latency increased: faster background execution still competes with
interactive work. Registration proposals and other NumPy calculations remain
outside the native proximity kernel. This single pair establishes an observed
improvement for this fixture, not a general speedup for all similarity searches.

The large STL fixture's previous similarity run was interrupted after more than
three minutes. At inspection its eight fingerprints were ready, while all four
runs still had candidate verification work. A stack sample located active work
in Python's surface-tree construction. That interrupted run supplies no complete
baseline timing and is excluded from speedup calculations.
A native-proximity run before the final array-boundary fix was also interrupted
after eight minutes while verification continued to make progress. Neither run
establishes a complete large-mesh similarity comparison; the final combined
optimization was measured on the small fixture above.

## Required Rust engine

The required `printstash-mesh-native` extension moves triangle coverage and
depth selection into Rust. It also parses 3MF mesh coordinates and face indices
from a stream, reads binary STL geometry, measures mesh bounds and volume, and
hosts the native G-code metadata binding. The independently reusable G-code core
and its libbgcode adapter are described in [Native G-code metadata engine](native-gcode.md).
Rust owns camera selection, normals, lighting and image encoding. Stored and DEFLATE model parts inside 3MF packages are read and
decompressed in Rust using `zip`, `flate2` and `zlib-rs`. The XML parser reads
64 KiB buffers without Python callbacks and retains size limits and CRC checks.
Package inventory validation and scene assembly remain in Python. Bzip2 and LZMA
members also decompress through the Rust ZIP library.
Outer ZIP inspection and extraction use the Rust archive core. STEP tessellation
uses the existing supervised Python helper; storage and job coordination remain
in the Python application pipeline.

Both Docker variants build and install the required Rust extension. Source
checkouts need Rust, Cargo, and a C++17 compiler before `uv sync --extra dev`;
CI and Docker use Rust 1.98.1 and GCC. The build toolchains are needed to build
the wheel, not to run it; the resulting Linux wheel links the C++ runtime and
statically includes its pinned zlib/Heatshrink codec code.

Backend rendering, thumbnail encoding, import admission and the import executor
require Rust. The renderer, loader and geometry engine selectors have been
removed. Missing or outdated extensions no longer select Python. Python remains
the application and format-dispatch layer; Three.js remains in the browser.

Compare complete imports across revisions with the same archive and resources:

```bash
uv run python scripts/bench_import.py /path/library.zip --output before.json
uv run python scripts/bench_import.py /path/library.zip --output after.json --compare before.json
```

The report records the Rust engine and compiled module hash. It also records generated preview hashes and checks them when the reference
contains them. A missing extension fails before the benchmark starts.
Add `--similarity` to both commands to compare the same import settings with
analysis enabled. Fingerprint completion is included in the total timer when this option is used.

The native rendering kernels run on one thread and release the Python interpreter
lock during computation. The current extension prepares angle-weighted vertex
normals and retains depth, RGB and reusable 32-bit face indices in a Rust frame
across draw batches. It returns the completed RGBA buffer once. This removes
repeated copies of depth and intermediate visible fragments. An interrupted or
failed draw cannot publish a partially updated native frame.

The owned native preview path also centers vertices, groups coincident positions,
projects the camera view, culls faces and computes crease-aware corner normals.
It receives the mesh once and processes drawing batches inside Rust, without
per-batch Python callbacks or triangle/normal array transfers. Position and face
indices use 32 bits; coordinates use float32 and accumulated normals use float64.
Camera and material values come from the versioned preview recipe. The native image path uses `fast_image_resize` for SIMD resizing and `image`
for PNG and lossless WebP encoding. It does not create another image thread pool.

Python lighting callbacks and older native extension adapters have been removed.
Native functions accept immutable inputs and validate dimensions, indices and
finite values. Rust uses no unsafe blocks; PyO3 supplies the Python boundary.

Similarity verification also uses a native immutable bounding-box tree when
available. Both construction and closest-surface queries release the Python
interpreter lock. The kernel retains the exact triangle projection algorithm,
limits each tree to 2,000,000 triangles and each query to 5,000 points, and
enforces a budget of at most 32,000,000 triangle tests. It rejects malformed,
non-finite or degenerate geometry before returning a tree. The native tree is a
required capability; a missing or outdated extension produces a stable
capability error instead of selecting another implementation. Analysis strips
Trimesh array tracking hooks at the numerical boundary, before cleanup and
registration; the caller's mesh remains unchanged.

The renderer still copies input buffers to give Rust immutable data while the
interpreter lock is released. Mesh loading and vertex preparation retain arrays
that grow with the individual mesh. These bounds do not make import memory
constant or establish performance for a 50 GB library.


## Streaming 3MF loading

The required native parser reads model XML from the ZIP in blocks of at most 64 KiB. It
retains numeric vertex and face arrays plus the scene XML needed to place them.
It does not decompress unrelated previews or project settings. Source installs
require the same Rust parser as the container images.

The scene loader preserves repeated instances, component transforms and
references to other model parts in the package. It rejects missing references,
cycles and resource-limit violations instead of returning a partial scene.
The loader follows the package model relationship when present, including a
non-default root model part, and supports the conventional `3D/3dmodel.model`
fallback. Units, component transforms and build-item printability are preserved.
Its default limits are 512 MiB of uncompressed package data, 4,096 ZIP members,
4,096 objects, 10,000 mesh instances and 512 MiB of expanded geometry arrays.
The XML parser also limits nesting to 256 levels and retained scene XML to
16 MiB per model part. These limits bound admitted work, not total process RSS.

The renderer discards back-facing and degenerate triangles before calculating
per-corner shading normals. Whole-mesh vertex normals still use all faces, so
this earlier culling retains the existing shading and silhouette behavior.
Geometry measurements and stored source files keep the complete mesh.

The report records native loading for 3MF and binary STL. ASCII STL, OBJ and STEP
retain their format loaders; these are not alternate implementations selected
when the native extension is missing. Compare changes using the complete archive
and the same renderer, metadata requirements and resource limits.

## Binary STL recovery without block callbacks

For exact binary STL files that exceed full-mesh admission, Rust owns both source
passes. The first pass validates coordinates, measures bounds and retains the
same deterministic 4,096-point framing sample. The second pass projects bounded
blocks into one retained depth buffer. Rust then shades the completed depth
buffer and encodes the image without
allocating the former NumPy neighbour arrays. The encoded image crosses into
Python for atomic publication. The worker retains its source,
triangle, candidate, memory and deadline limits. It checks source identity before
and after each pass and rejects changed files or incomplete renders.

The bounded fallback job reads binary or ASCII STL, rasterizes its sample,
resizes and encodes the preview entirely in Rust. It reports partial coverage
when sampling or byte limits prevent reading the whole source.

## Binary STL and geometry measurements

The native binary STL loader reads the file in 64 KiB blocks and writes
coordinates and face indices directly into their final buffers. It preserves
stored triangle order and rejects nonfinite coordinates. Binary detection uses
the declared triangle count and file length, so a binary header beginning with
`solid` still loads correctly. ASCII STL uses the existing loader. The native
loader limits both file bytes and expanded vertex/index buffers to 512 MiB;
application admission limits still apply before loading.

Native geometry measurement computes bounds of referenced vertices and signed
surface volume in blocks of at most 65,536 faces. It avoids unused inertia and
center-of-mass arrays. Dimensions and positive volumes keep the existing
rounding to two decimal places.

The geometry calculation retains the existing convention for open surfaces;
it does not turn an open mesh into a watertight solid. STEP tessellation still
uses its existing worker, after which the native measurements can process the
resulting triangles. The source files and rendering quality are unchanged.

Preview-only mesh processing verifies mesh reclamation through a weak reference.
It first collects younger object cycles and runs a full collection if the mesh
is still alive. This avoids repeatedly scanning unrelated long-lived objects.
Allocator trimming remains enabled. Fingerprint analysis retains full collection
because it can create additional mesh copies.

Connected-component extraction uses `petgraph`'s union-find implementation
with compact edge and face indices. It preserves edge connectivity and canonical
component ordering, including nonmanifold edges and vertices that touch without
sharing an edge. Other similarity algorithms and model
inference retain their existing implementations.

## Lossless preview compression

Preview recipe version 3 uses WebP method 0 for both rendered and normalized
embedded images. It preserves every RGBA value, including colors under fully
transparent pixels, while spending less CPU time on compression. The tradeoff
is larger thumbnail files. The previous recipe used method 6, which spent more
CPU time looking for smaller encodings.

The canonical recipe is shared with the generated frontend profile. Its new
fingerprint keeps newly generated thumbnails separate from previous outputs;
existing thumbnail objects remain immutable. The encoder is the existing native
WebP library. Rust continues to handle the mesh operations described above.

## Background scheduling and admission

The source writer no longer computes previews ahead of ordered publication.
It extracts or downloads one selected item, saves its source and durable work
requests, then advances. Metadata and thumbnail workers can share one source
materialization and mesh parse while keeping independent result states.
See [ingestion architecture](architecture/ingestion.md) for ownership and recovery.

The local scheduler runs one enrichment unit at a time. Shared compute permits
bound aggregate activity with similarity work. Filesystem reservations check
space for source materialization and output publication; mesh processing keeps
its existing memory admission. These limits do not establish a hard process RSS
ceiling. Administrators should leave headroom for other services.

`--workers` remains in the harness for comparisons with the previous prefetch
implementation. The report preserves the requested value, but the redesigned
source path does not use `VAULT_IMPORT_WORKERS` to create geometry workers.
Do not interpret a larger value as extra background concurrency.

Compare repeated runs with the same archive, native module, preview recipe and
measurement protocol. Check final source hashes, dimensions, volumes, triangle
counts and preview pixels before interpreting timing differences. Report time
to availability, total time, CPU, memory and library latency separately: earlier
source success can coexist with more total work or slower navigation.

## Native image processing

The required extension also handles final thumbnail normalization for PNG, JPEG
and WebP: bounded decoding, alpha bounds, crop, resize, transparent canvas and
lossless WebP encoding. Images over 25 million source pixels are rejected before
pixel decoding; decoder allocations have a separate 256 MiB limit. Empty images
retain the existing validation error. Other input formats are rejected without invoking Pillow.
Already canonical WebP thumbnails are validated and reused without reencoding.

Resizing uses Lanczos3 with alpha handling for ordinary previews and independent
channels for the recovery silhouette. Depth shading reads neighbouring samples
and smooths normals in Rust with bounded buffers. PNG and WebP remain lossless;
changing the resize implementation can change edge pixels, and changing the
encoder changes file bytes. Comparisons therefore check decoded pixels as well
as source hashes, geometry and processing outcomes. Exact compressed-byte parity
is not a promise across these image engines.


## Controlled migration comparison

M00 adds `scripts/bench_corpus.py` and `scripts/bench_matrix.py`. The opt-in
`benchmark_imports` input on the CI workflow runs a dedicated Ubuntu runner:

```bash
gh workflow run ci.yml --ref feature/rust-m00-baseline \
  -f benchmark_imports=true \
  -f benchmark_parent=4b9afeb92d4e7e24298454af38c6c76aeddec437
```

The runner builds the original, immediate-parent, and current committed full
release images before measurement. It records immutable image IDs and retains
release image archives alongside sanitized evidence for 90 days. The same
hash-pinned psutil instrumentation and v5 harness are layered over each release;
application dependencies are not upgraded. Packaged source directories receive
read/traversal permissions in the instrumentation layer so an unprivileged runner
can hash their unchanged bytes. Subsequent milestones must retain the
M00 image archive and corpus: rebuilding a moving Docker base or regenerating
fixtures with changed numerical dependencies is not an equivalent baseline.
When `benchmark_imports` is true, ordinary CI, browser, image-build and scan jobs
remain skipped so they cannot contend with the controlled measurement cells.

The deterministic corpus covers small STL/3MF, 128 small meshes, a 327,680-face
mesh, a mixed large archive, existing Prusa/Orca/BGCODE fixtures, a STEP fixture,
and similarity candidates. Every source and archive digest is recorded. This is
a synthetic scaling corpus, not a representative user library. Optional real
inference assets, labeled similarity-quality evaluation, remote storage, and queue
recovery are separate required workloads; this runner does not establish them.

Each database runs sequentially under total budgets of 2 CPU/2 GiB and 4 CPU/4 GiB.
PostgreSQL receives one quarter of that budget on a private internal Docker
network, with no published ports. Application containers receive the remainder;
SQLite containers receive the full budget. No container receives a Docker socket
or host networking. Each import uses a fresh database and storage directory.

For every case the runner checks output parity, performs a warm-up for each
revision, then seven alternating before/after pairs. It extends to fourteen pairs
when elapsed/API-p95 variation exceeds 5%, or CPU/memory variation exceeds 10%.
Raw reports retain API p50/p95/max, upload/extraction/processing times, CPU,
sampled RSS and cgroup memory peaks. PostgreSQL CPU includes the entire benchmark
CLI lifecycle; its memory peak is cumulative for the service profile, not a
per-import measurement. Neither is silently combined with application-only
measurements. Sampled RSS can miss short peaks and double-count shared pages.

`comparison.md` and `comparisons.json` expose paired changes, spread and threshold
exceedances. A green measurement job establishes successful execution and output
parity, not accepted performance. Repeatable regressions above the plan's 5%
elapsed/API-p95 or 10% CPU/memory limits still block merging. Shared-runner timing
noise must be resolved in review, not converted into flaky CI assertions.


## Durable queue baseline diagnostic

From `backend/`, run:

```bash
uv run python scripts/bench_queue.py --database sqlite --output queue.json
uv run python scripts/bench_queue.py --database postgres --output queue-postgres.json
```

The probe uses the application's current Python coordinator and durable database
repositories. It records bounded acceptance, claim and completion latencies, idle
polling CPU, and recovery after killing a real claimant. Recovery waits for the
production 120-second lease; deadlines are not shortened for a faster result.
Its default fault diagnostic requires rollback isolation, preserved identity,
rejection of stale callbacks, and completion by the successor before succeeding. Each run owns a
fresh migrated database; internal subprocess modes require the supervisor's
matching private database receipt. PostgreSQL supports the same isolated
maintenance-service option as the import benchmark.

This does not execute import payloads, measure API latency, exercise contending
claimers, or qualify a replacement queue. Those have separate import and M01
requirements. A stale callback currently exposes a correctness failure at the
original M00 baseline: it poisons the status cache and suppresses the successor's
completion. The diagnostic rejects that sequence instead of accepting its timing.
The M00 cache invalidation fix has a failing-before/passing-after regression.

Use `--steady-state` for a separate, comparable measurement of ordinary acceptance,
claims, completion and idle polling. This mode still verifies durable completion
counts, unique claims and rollback, but explicitly reports `fault_injection` as
`not_run` and emits no recovery or stale-callback verdict. Its distinct
`durable-queue-steady-v1` protocol cannot be compared with fault-diagnostic output.
The controlled matrix uses this mode; the independent default diagnostic and
real process-kill E2Es retain all recovery assertions. A valid normal-operation
timing does not turn the original baseline's failed crash case into a pass.

The controlled matrix has four sequential CI profiles (SQLite/PostgreSQL, 2/4 CPU)
with matching 2/4 GiB budgets. Every profile builds before measuring, performs one
warm-up and seven alternating pairs, and expands noisy cases to fourteen pairs.
Queue comparisons require the same steady-state protocol, database version and
completion outcomes. Release images are archived before measurements, and their
artifact is retained even when a later workload fails. Failed correctness checks
block that workload's timing acceptance; rerunning a known baseline failure does
not establish performance evidence.

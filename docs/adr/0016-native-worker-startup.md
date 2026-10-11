# ADR-0016: Reduce unused imports without reusing native parser state

Status: accepted — defer parent-only instruments and database URL parsing at their existing owners; retain fresh guarded children.

## Decision

Keep one disposable native process per Artifact request. Import mesh observations only where the parent records admission, completed supervision or validated phases. An inherited child admission retains the same permit checks and does not construct the parent's Prometheus registry. Import SQLAlchemy's URL parser inside `_sqlite_db_path`, after its existing non-SQLite return; actual directory creation and SQLite URL semantics remain unchanged. Settings validation, worker requests, native libraries and outputs retain their existing contracts. No interpreter pool, parser loop, launcher service, schema, recipe or dependency is added.

The normal mesh request already shares source preparation across metadata, thumbnail and optional fingerprint. Embedding and visual requests already share preparation across their views. Counting one process per view or one source parse per primary output would invent a batching benefit.

## Measured choice

The cost gate was declared before measuring: at least 1.10× median improvement and 50 ms saving in complete fresh-worker execution for each evaluated input, with exact CPU outputs, geometry and source bytes plus unchanged parent observations and containment. These are exploratory medians for synthetic small cubes on a nonexclusive WSL host, not a whole-Artifact SLA, percentile or large-model performance qualification.

Each sequential arm has 30 samples per input. The first candidate, deferring only parent metrics, observed 1.0473× for STL and 1.0222× for 3MF and failed the cost gate. A separate import trace identified the only SQLAlchemy use in configuration as the directory helper; its eager import was unused in these workers. The final candidate combines those two changes.

| Input | Baseline median ms | Final candidate median ms | Improvement | Saving ms |
|---|---:|---:|---:|---:|
| cube binary STL | 2,247.07 | 2,021.42 | 1.111629× | 225.65 |
| cube 3MF | 2,388.84 | 2,000.70 | 1.194004× | 388.14 |

One bounded confirmation alternated both input and arm order using the same hot parent and fresh supervised children. The baseline child used a full application snapshot with the exact three original files from commit `35de3cf6407414b4bf5fee3705a502c4efc0d610`; unchanged shared core, dependencies, source bytes and exported private configuration remained common. No production file was edited while a child imported it. Ten samples per method/input confirm the gate:

| Input | Alternating baseline median ms | Alternating candidate median ms | Improvement | Saving ms |
|---|---:|---:|---:|---:|
| cube binary STL | 2,299.56 | 2,061.89 | 1.115269× | 237.67 |
| cube 3MF | 2,413.56 | 2,171.49 | 1.111477× | 242.07 |

All 220 mesh observations completed; source and output digests are retained in [samples.csv](0016-startup-pilot/samples.csv). The candidate contract tests separately assert analytical geometry and volume, exact CPU WebP bytes, immutable inputs and following work after invalid input. Every timing sample, including the first, remains in the report. Filesystem cache temperature was uncontrolled. The observed differences in engine time are retained and cannot all be attributed to imports; the outside-engine residual includes launch, imports, framing and retirement rather than a pure import span.

A separate 30-sample admitted bootstrap/guardian/stdlib-reply control measured a 95.526 ms supervised median. This excludes actual worker-module and numerical imports. A stdlib launcher that still performs exec cannot inherit those numerical imports. A numeric-preloaded fork launcher needs a separate proof of native-thread and lock safety, child groups, guardians, immutable memory limits, source/native permits, result framing and failure recovery. Observing one thread does not supply that proof. Persistent parser state across Artifacts remains unqualified and is not introduced.

## Validation and reproducibility

The [test matrix](../testing/native-worker-startup.md) covers real fresh-child operation with parent dependencies refused at an external import boundary, parent telemetry and URL semantics, plus real hard-limit, parent-death, descendant, cancellation and final-exit regressions. Import traces run separately from timed samples. No GPU, runtime migration or older-Python support is involved.

The existing collector runs as `python -m scripts.bench_mesh_pipeline --mode worker --runs 30 --case cube-binary.stl --case cube-mm.3mf`; its isolated vault and real supervisor include startup and cleanup. Reproduce the baseline on the recorded commit with the same dependency environment. The reverse-selector metrics-only arm and bounded alternating confirmation are identified in [summary.json](0016-startup-pilot/summary.json), with relative source identities in [code-identities.json](0016-startup-pilot/code-identities.json) and guarded-launch observations in [launch.json](0016-startup-pilot/launch.json). Installation paths and private model names or bytes are excluded.


## Persistent compute ownership

The [portable compute broker](../compute.md) extends lifetime only for validated array execution and verified model sessions. Disposable source parsers, hard limits, source permits and retained RAM reservations remain unchanged. A private descriptor-owned broker amortizes graphics context, shader and model startup across sequential Artifacts without retaining untrusted parser state.

Inference coalesces already-ready inputs. Visual thumbnail gathering reads existing derivatives; bounded similarity component groups share one source parse. No extra large source is parsed merely to fill a batch. Jobs, generation leases and run writers retain scheduling and publication authority. Broker queues remain transient and are discarded on restart.


## Render owner follow-up (2026-10-10)

GPU context ownership is persistent and separate from disposable parsers. The
supervisor warms the descriptor-elected owner before launching native parsing,
so the broker does not inherit a parser's address-space ceiling. Subsequent
Artifacts reuse the context, shaders, geometry and framebuffer pools. Render
startup does not initialize ONNX. WSL uses the maintained Mesa GL/D3D12 route,
with independent DXCore hardware evidence; see [compute deployment](../compute.md)
and [render evaluation](../testing/compute-render-wsl.md). Cold-start cost and
warm work remain separate observations; operator visual acceptance is pending.


The render owner also retains postprocessing shaders and per-resolution data.
Validated host geometry uses a bounded pinned cache, while active parser permits
remain owned until their processes and buffers exit. Binary IPC can reference
this geometry across cameras; broker restart naturally requires a fresh upload.
Canonical CPU finalization, when the GPU canary refuses it, runs in the already
reserved caller. No source parser state is pooled across Artifacts.

## Ready-model preparation follow-up (2026-10-11)

Disposable exec now receives and restores the parent's scheduling priority.
Asynchronous mesh-worker outputs separately use background compute priority so
already-admitted frames can coalesce; Job/native admission retains its original
priority. Source parser lifetime and hard worker ceilings remain unchanged.

An optional bounded preflight validates small ASCII STL/OBJ sources before using
the existing weighted native claim. Unknown syntax, exceeded bounds and changed
sources retain whole-pool admission. Identity-only scene loading avoids a copy;
independent STL facets retain canonical welded topology/volume validation; normal
preparation reuses computed lengths. DBOS queue dispatch polls every 0.25 seconds
by default through deployment configuration, independently of recovery scanning.

The [batching/preparation evidence and matrix](../testing/compute-model-batching.md)
record these corrections and the earlier 1000-model aggregate baseline. The next
comparison of those cached models remains pending. Persistent untrusted parser
state, further GPU preparation candidates and operator visual acceptance are not
qualified by these changes.


Deferred normal preparation keeps the parser's source arrays and RAM permit alive
until compute completion or canonical fallback. Only validated relative geometry
crosses IPC; CSR construction, shader sessions and GPU buffers belong to the
persistent owner and its separate reservations. Fresh workers defer optional
format, fingerprint and inference imports, but still import their necessary
numerical/parser dependencies. These changes do not introduce preloaded forks or
persistent untrusted source parsers.


## Release parser capacity before GPU waits (2026-10-11)

Thumbnail-only work can hand off an owned, bounded geometry packet after its
source parser exits cleanly and the supervisor reaps its process tree. Only then
is the native parser claim released. Staging retains the existing prepared-byte
reservation and a four-slot/192MiB sublimit, with custody descriptors inherited by
the guardian. The lightweight supervisor dispatches validated framing to the
broker and reacquires native admission for a disposable encoder or CPU fallback
using that same packet. Existing Job authority and the absolute deadline remain
unchanged. Fingerprint work retains the combined worker path.

This extends neither untrusted parser lifetime nor configured Job concurrency.
The encoder adds a process startup, so per-handoff telemetry records its queue
and complete supervision cost separately; only a matched source-to-publication
benchmark can establish whether the change improves ingestion. Numerical imports
are not preloaded into a fork server. The earlier startup measurements and their
limits remain historical evidence rather than qualification of this new handoff.


### Measured scope of the handoff

The initial 200-model implementation staged every eligible request. It halved
broker execution but increased total source-to-publication time by about 28.5%,
with 399 native admissions for 200 sources. The final policy therefore uses the
separate stage only when the existing native estimate claims the entire memory
pool; smaller requests keep their combined disposable process. The exact same
preflight plan is consumed at native admission rather than recomputed. This does
not lower unknown-complexity reservations or relax process limits.

The RGBA encoder has a minimal import boundary verified in a fresh interpreter:
no configuration, Pydantic, NumPy or source parser libraries. CPU fallback remains
behind the existing media rendering facade in a guarded worker. See the
[measurement and regression record](../testing/compute-native-pipeline-200.md).

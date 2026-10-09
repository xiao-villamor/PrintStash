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

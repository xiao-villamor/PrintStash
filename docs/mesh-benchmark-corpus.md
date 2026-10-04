# Reproducible mesh benchmark inputs

The checked manifest at `backend/benchmarks/mesh/corpus-v1.json` freezes fourteen
small synthetic inputs. Generate their bytes and a portable copy of the manifest:

```sh
cd backend
uv run python -m scripts.mesh_benchmark_corpus --output-dir /tmp/mesh-corpus
```

The generator uses the standard library, explicit little-endian STL records,
fixed ZIP timestamps/permissions/order, and uncompressed ZIP members. Its output
does not depend on trimesh, a slicer, the clock, random state or a native parser.
Every file has a SHA-256, byte size, source format, origin and repository license.
`source_faces` counts complete encoded triangles, including unreachable mesh
resources; the truncated STL has eleven complete records and an incomplete last
record. Resources are null for STL. Instances are null for a cyclic scene whose
expansion has no finite count.

## Target contracts, not a compliance claim

`expectation_scope` is `target_contract_not_observed_compliance`. Expectations are
independent acceptance targets. A parser accepting an input with an expected
refusal is an unresolved contract violation, not a successful validation. The
fixture-generation tests certify the inputs and expectations, not current parser
conformance. Benchmark render outcomes remain separate observed data.

The corpus includes binary STL with an ordinary or `solid` header, a truncated
facet, a mismatched declared count, ASCII whitespace/exponents and an incomplete
ASCII facet. 3MF cases cover millimeters/inches/microns, multiple build instances,
a remote unused vertex, reflection, a component cycle and an unsupported required
extension. The cube has twelve outward-oriented triangles. Physical expectations
come from cube dimensions, unit conversion and disjoint-instance addition:
20 mm cubed is 8,000 mm³; one inch cubed is 16,387.064 mm³; one micron cubed is
10⁻⁹ mm³. Two 20 mm cubes separated by 40 mm have 60 × 20 × 20 mm bounds and
16,000 mm³ volume. Reflection preserves physical volume magnitude.

Accepted cases specify relative tolerance `1e-6` and absolute tolerance zero.
This prevents a collapsed microscopic measurement from passing because an
absolute tolerance exceeds the expected value. Refusals name the violated rule,
not an invented current implementation error code.

The generator and manifest must agree byte for byte. Changing a published input
or expectation requires a new corpus version and explicit baseline decision;
do not silently refresh hashes to hide drift. V1 includes no third-party assets.
The separate [v2 corpus](mesh-benchmark-corpus-v2.md) adds large/degenerate/nonfinite
meshes, production-extension cases, pinned real slicer references and recovery
contracts without changing these original inputs. Described recovery scenarios
are distinct from executed recovery tests.

## Measure these inputs

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --extra full python -m scripts.bench_thumbnails \
  --contract-corpus --cold-runs 1 --warm-runs 1 > /tmp/mesh-smoke.json
```

The microbenchmark JSON schema is version 4. It embeds the contract manifest, actual input
hashes and every render/read attempt, including errors and elapsed cost. The
existing shape profile and `--quick` remain available; they have no frozen
contract manifest. An external model is identified by its actual hash and is not
silently added to the licensed synthetic manifest.

The environment snapshot records Git commit and dirty state, Python/platform,
CPU model/count/affinity, host RAM, visible cgroup CPU quota and memory limits,
thread environment settings, renderer dependency versions, thumbnail recipe, output
resolution and repetition counts. Package versions include SciPy and DBOS as well
as NumPy, trimesh, Pillow and printstash-core; optional STEP packages cascadio and
cadquery-ocp-novtk are null when absent. A recorded dependency version describes
the environment and does not imply that this thumbnail-only request executed
that package. The request flags explicitly disable geometry and fingerprint
outputs; their recipe attribution is null rather than naming an unapplied recipe. A missing Git executable or unavailable
platform information produces null, not an invented value. Cgroup v2 limits
include the current visible group and ancestors; conventional v1 mount-root
values are a fallback. Hidden ancestors and nonstandard v1 controller mounts
are not inferred. `cpu_limit_read` and `memory_limit_read` distinguish unavailable
limits from readable unlimited values. Host RAM is not claimed as a container's
memory budget.

Each render sample retains the engine's ordered `phase_stats`: elapsed
nanoseconds, known input/output sizes, triangle count and completed/failed
outcome. The engine's stage spans include nested work and exclude uninstrumented
gaps; do not add them to the outer sample duration. Byte counters represent
known logical sizes, not measured I/O traffic. Unknown sizes remain null. If the
engine raises before returning, stage evidence is an empty list, not fabricated
zero-time stages. If image validation/encoding fails after the engine returns,
its stage evidence remains alongside the attempt's error. The outer render timer
includes this validation/encoding work. Parent worker costs are not part of this
microbenchmark's phase fields.

`corpus_order` lists the actual input order. Per-lane `sample_index` starts at one.
Every input is processed sequentially: all renders first, then publication of
the last render's output as untimed setup, then all persisted reads. If the last
render fails, no earlier output is silently used as a cache hit. Storage is a
private temporary LocalStorageBackend per input, removed after its reads; every
read uses the delivery plan and consumes the complete persisted body, verifying
its bytes against the published output. This is an application representation
cache populated by the benchmark. The historical `--cold-runs` and `--warm-runs`
names do not imply cold OS caches, warmed worker startup, or a mixed/randomized
execution order. Medians summarize successful attempts only; every failure and
its elapsed cost remains in the raw samples.

These remain same-interpreter engine renders and local persisted representation
reads. Filesystem caches are uncontrolled, and RSS is the current process's
lifetime high-water mark. No HTTP, database lookup or worker startup cost is
included. See [mesh execution telemetry](mesh-telemetry.md) for native worker cost
attribution. A target refusal and a render failure are different concepts: this
CLI does not turn their coincidence into a parser-conformance pass.

`performance_gate_qualified` is false. A smoke run validates the measurement
path; it does not certify latency or percentiles. Use at least 30 observations
per cell for exploration and 100 for a latency gate, with controlled hardware,
limits, cache policy, order and quality checks. Record dispersion and failures;
do not discard timeouts to improve a median. Worker-level, upload-to-visible,
concurrency and soak baselines remain separate evidence.

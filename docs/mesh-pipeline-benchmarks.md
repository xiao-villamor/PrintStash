# Mesh pipeline measurement paths

Use separate measurements for thumbnail rendering, the complete native worker,
and availability through the application. These paths answer different questions;
their times and output hashes are not interchangeable.

```sh
cd backend
# In-process engine plus an actual persisted-representation read.
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --extra full python -m scripts.bench_thumbnails \
  --contract-corpus --cold-runs 1 --warm-runs 1 > /tmp/mesh-micro.json

# Fresh supervised native process for each sample, including decode and cleanup.
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --extra full python -m scripts.bench_mesh_pipeline \
  --mode worker --corpus v2 --case cube-binary.stl --runs 5 > /tmp/mesh-worker.json

# Real production lifespan, SQLite and DBOS in a temporary local vault.
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run --extra full python -m scripts.bench_mesh_pipeline \
  --mode ingestion --corpus v2 --case cube-binary.stl --runs 5 \
  --deadline-seconds 180 > /tmp/mesh-ingestion.json
```

The worker/ingestion CLI emits schema 1 JSON to stdout and diagnostics to stderr.
`--corpus v1` preserves the original fourteen inputs. V2 defaults to the small
offline profile; `--full` also materializes grids through two million triangles.
`--download-external` explicitly downloads the three revision/hash-pinned real
slicer projects. Both flags require v2. The manifest lists only materialized
inputs, while external references and recovery contracts remain separate.
See [v1 corpus](mesh-benchmark-corpus.md), [v2 corpus](mesh-benchmark-corpus-v2.md)
and the [behaviour matrix](testing/mesh-pipeline-benchmark.md).

## Worker observations

Each observation preserves source hash/length, repetition index, engine duration
and child high-water RSS when available, sequential phases, parent supervision
time, sampled process-tree RSS, exit cause, reply size and actual image
format/dimensions/hash. The parent duration includes admission, startup, native
execution, decoding and cleanup. Subtracting engine duration does not isolate
startup. Phase bytes are known logical input/output sizes, not measured I/O or
live RAM. Unknown counters stay null. Phase times include nested native work;
adding them to supervision time would double count work.

The output boundary is the raw worker reply. An embedded preview can retain its
native dimensions/format. The ingestion output boundary is the visible canonical
thumbnail after normalization and publication. Compare image quality at an
explicit common boundary rather than equating raw and canonical output hashes.

## Ingestion observations

The benchmark clears inherited `VAULT_*` settings, loads production defaults in
an empty directory, and pins the effective private settings for child processes.
Its database, keys, originals, staging and derivatives live under a temporary
vault. Similarity/fingerprint work is explicitly disabled. The requests use ASGI
transport with no network latency, a real production JobEngine, real SQLite and
local storage. It neither imports a test engine nor substitutes storage writes.
TestClient buffers request/response bodies; its harness costs are included and
these measurements do not establish network streaming memory or throughput.

Bootstrap time includes migration, application startup and owner setup. It is
reported separately. Per-sample milestones start before upload: acceptance,
first observed committed Artifact, metadata READY, and the first fetched valid
thumbnail. Availability marks are upper bounds sampled every 50 ms; they are
not SQL commit timestamps. `elapsed_ms` ends with the observation and excludes
the subsequent original download/hash audit, which has its own duration and
`original_verified` result. Shutdown also lies outside the sample interval.
The interval includes the benchmark's source-presence query before upload;
`source_probe_ms` identifies that instrumentation cost. If the query fails, the
attempt retains its cost and the source presence stays unknown.

Repeated uploads use the same bytes and the same private vault. The upload
endpoint can create distinct Models and Artifacts. `source_preexisting` records
whether matching source bytes existed before upload; `artifact_reused` reports
whether the returned Artifact actually reused a preceding identity. Neither
flag proves a cold native execution or a persisted derivative cache hit.

The flow does not capture native phase events per Job: the protocol reports
`native_phase_collection=none`. Use worker mode for native attribution. A flow
deadline censors the observation; it does not cancel the accepted Job. Earlier
Jobs can remain active while subsequent samples run in the shared private vault.
The report declares this carryover. After all samples, active private Jobs are
cancelled through the API. Cleanup waits for physical mutations and storage
reader leases to drain, holds admission through lifespan teardown and checks
the counters again. Cleanup costs and cancellations have a separate report.
If cleanup fails, the report retains the vault path and errors, the CLI exits 1,
and the workspace is preserved. Unexpected startup/teardown exceptions also
preserve the workspace and print its path to stderr. Successful cleanup permits
removing the private workspace. Job terminal state alone is not proof that a
synchronous step or native child has ended.

## Failures and performance claims

All attempts appear in the report and summary. Input/geometry/resource refusals,
execution failures and deadlines have distinct outcomes. Partial milestones and
measured costs survive failures; absent images, phases and RSS are never invented.
Target expectations are independent contracts, not observed parser compliance.
Known derivative input refusals retain their typed causes. Worker, storage,
renderer and unknown producer failures count as execution failures. A native
timeout keeps its cause rather than becoming an availability-polling deadline.

The environment snapshot records commit/dirty state, CPU affinity/quota, visible
memory limits, host RAM, package versions and thread settings. Native math threads
are pinned to one. OS filesystem cache remains uncontrolled, and repeated inputs
can reuse filesystem pages. Inputs are generated and hash-verified before
sampling, which can warm those pages. These smoke runs are not qualified performance gates.
Use the frozen hashes, explicit boundaries and all failure counts when establishing
controlled baselines or comparing implementation changes.

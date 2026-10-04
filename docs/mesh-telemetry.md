# Mesh execution telemetry

Mesh workers return bounded stage costs in their final reply. The supervising
process exports bounded JSON records through its production text logger to
stdout. `mesh_supervision` and `mesh_phases` records share a generated 32-character
execution ID and include the emitting process ID and role. Collect worker
container/process logs to retain this evidence in split deployments.

The existing process-local Prometheus registry receives the same costs. In a
unified process, or an API configured to execute jobs, its `/metrics` endpoint
exposes these counters. Standalone `app.worker` processes have no HTTP scrape
endpoint: their registry is not visible at the API scrape. Structured stdout
records are the export path for that topology.

## Recorded costs

The engine records these stages when reached: admission checks, embedded preview
extraction, mesh load, measurements, fingerprint extraction, full render,
streaming preview, and fallback preview. Metadata-only and fingerprint-only
requests retain their stages even though they produce no thumbnail.

Stage spans are sequential and do not overlap. Each includes nested native work
inside that operation; for example, load may include STEP conversion, and render
includes image encoding. They exclude gaps, cleanup and uninstrumented work.
Do not add stage timings to parent duration, or interpret them as kernel-level
profiling. Repeated visits aggregate into one entry per stage.

A stage includes elapsed nanoseconds, known input/output sizes, known triangle
count and a closed completed/failed outcome. Sizes describe the operation's
inputs or results, not measured filesystem traffic. Unknown sizes and counts
remain null. No phase-local RSS peak is claimed.

The parent independently records time from process setup through cleanup,
reply bytes actually received, largest sampled resident size of the entire
child process tree, and process exit cause. A SIGKILL is classified as `sigkill`,
not evidence of OOM; `memory_limit` means RSS exceeded the budget or the bootstrap
reported its explicit resource exit. Sampling can miss brief peaks;
unavailable RSS remains null and is omitted from the RSS histogram. These
values remain attached to native worker errors and cancellation exceptions.
Operating-system failures during supervision or cleanup become `worker_failed`
errors with `supervision_failed` costs; unexpected programming exceptions retain
their original type and only export their observed costs.
They are distinct from the child's existing process-lifetime high-water RSS.

There are no progress frames. If a child times out, exceeds memory, or crashes
before returning its final reply, its active phase and completed stage costs are
unknown. The parent retains and exports the observed process costs without
inventing phase attribution. An exit-zero process can still return invalid data:
its process exit cause remains zero, while the caller receives `worker_failed`
with those same supervision costs.

## Metrics

| Metric | Meaning / bounded labels |
|---|---|
| `printstash_mesh_worker_exits_total` | Process exits by `cause` |
| `printstash_mesh_worker_duration_seconds` | Parent-observed duration by `cause` |
| `printstash_mesh_worker_peak_tree_rss_bytes` | Sampled tree RSS by `cause` |
| `printstash_mesh_worker_reply_bytes_total` | Received bytes, including incomplete replies, by `cause` |
| `printstash_mesh_phase_duration_seconds` | Child stage duration by `phase`, `outcome` |
| `printstash_mesh_phase_bytes_total` | Known sizes by `phase`, `direction` |
| `printstash_mesh_phase_triangles` | Known triangle counts by `phase` |

Labels never contain filenames, Artifact IDs, free-form request reasons, or
exception messages. Metrics are best-effort: an exporter error is logged and
cannot change the operation's outcome or prevent process cleanup.

## Reply compatibility

The existing terminal mesh frame now requires a `phase_stats` envelope with
version **1**. It permits at most eight distinct stage entries and 4 KiB of JSON.
Counters are nonnegative signed-64-bit integers; booleans, floating-point values,
unknown labels, duplicates and extra fields are rejected. A missing or unsupported
envelope is a failed worker reply, rather than silently fabricated empty stats.
The parent launches a fresh worker from the same application build; mixing old
worker replies with the new decoder is deliberately unsupported.

No rendering algorithm, derivative recipe, application API or stored metadata
changes. No new worker HTTP server, port, thread or telemetry database is added. This telemetry does not yet provide upload-to-visible latency, a frozen
benchmark corpus, kernel-level profiling, or partial derivative publication.

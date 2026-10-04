# Mesh processing safety

Mesh bytes remain the original Artifact even when enrichment is refused. Requests
serve originals through the storage contract. Derivatives enter through
`mesh_isolation`; viewer conversions through `stl_isolation`; verification through
`verification_isolation`; embedding inputs through `embedding_isolation` and
`visual_render`. The repository boundary test prevents new callers of the raw
geometry algorithms outside their implementation owners.

Uploads, archive/URL imports and remote sources commit an Artifact before
derivative jobs consume it. Mounted sources compare content hashes before
invalidating derivatives. Work Sources and the reconciler discover intent;
a nudge never resets an exhausted derivative.

## Containment

Each admitted process starts through a stdlib bootstrap. On Linux its
`RLIMIT_AS` is set before NumPy, trimesh, CAD or image imports. This limits
address space, which includes native mappings and differs from resident memory.
RSS monitoring counts the complete process tree; a wall-clock deadline defaults
to 300 seconds. A resource refusal is terminal for unchanged bytes and recipe;
timeouts retain the configured bounded retry policy.

Native admission shares one byte and slot budget across processes using the
same local data root. Descriptor ownership recovers stale records across reboots. Binary STL preflight reads the fixed header
and exact file size; its face count selects a memory weight. Unknown complexity
reserves the whole pool. Weights use the greater of the measured startup floor
and historical whole-pipeline cost per face, capped by the configured pool.
These are scheduling estimates; the hard process limit remains authoritative.

`max_render_jobs` limits simultaneous jobs without dividing the maximum mesh
size by that count. A large job can wait and use the whole pool. FIFO order starts
at registration and prevents later small jobs from continually overtaking a
large waiter. New capacity settings wait for prior active credits to drain.
Setting `mesh_memory_budget_fraction=0` disables geometry estimates while keeping
half of detected memory for containment; unknown memory uses a bounded 2 GiB pool.

Sources acquire a separate prepared-byte reservation before materialization or
native admission. `mesh_prepared_max_mb` defaults to 4096 MiB, accounting for two
copies per source and all sources of a verification pair together.
`mesh_prepared_max_jobs=0` follows `max_render_jobs + 1`, with at least two batches.
`mesh_source_io_jobs` defaults to two concurrent materializations. I/O slots close
when a path is ready; prepared bytes remain reserved while waiting for native
resources and through execution. Storage still reserves free disk space on the
actual workspace filesystem. Persistent Artifact caches retain their own leases.

Credits are held by locked file descriptions, explicitly inherited by native
children and their guardian. Releasing a parent handle does not free a child's
credit. Prepared source copies live under the reservation's private workspace;
an abandoned workspace is removed under a recovery claim before its credits can
be reused. Recovery I/O and cancellation checks run outside the coordinator lock.
See [ADR-0012](adr/0012-local-native-resource-admission.md) for deployment boundaries.

`mesh_admission` JSON and Prometheus admission histograms report queue duration,
requested resources and admitted/cancelled/deadline/failed outcomes separately
from worker supervision. Nested or inherited calls do not emit another queue
record. Process metrics retain their existing exit and memory observations.

CAD tessellation and STL streaming inside a mesh worker use its existing process,
budget and deadline. CAD output capacity belongs to the supervising parent,
which releases it on every exit; disposable workers never open the application
database for output admission. The parent owns the temporary directory and removes it
after killing and reaping the worker group. Linux supervisors adopt and reap
descendants from their own admission without waiting on another worker. Linux parent-death protection kills
an abandoned worker tree. Its stdlib guardian also removes abandoned temporary
output after checking the directory identity; a replacement is preserved. Unsupported hard-limit platforms fail closed.

Synchronous Job steps carry a scoped cancellation probe into admission and
native supervisors. They poll durable intent at most every 200 ms and check again
before accepting a worker outcome, including abnormal exits. Cancellation, a removed Job, or a superseded
attempt unwinds the process tree, temporary outputs and capacity before releasing
admission. Immediate retry cannot revive the cancelled execution: it belongs to
the prior attempt. Cancellation remains distinct from a malformed/resource
failure and preserves the original Artifact.

Keep framework, storage and job infrastructure out of the core geometry library.
The admission and bootstrap are application responsibilities.

Native STEP GLB conversion uses serial OpenCASCADE tessellation. Its own parallel
thread pool ignores OMP_NUM_THREADS and can exhaust address space through thread
stacks under small budgets; serial execution keeps it inside the admitted worker.
B-rep conversion propagates allocation failures to the resource-limit outcome.

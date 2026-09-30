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

The shared admission controller divides the configured memory allocation by
`max_render_jobs`. Runtime changes wait for current admissions to drain before
using the new split. Setting `mesh_memory_budget_fraction=0` disables estimates
while retaining a half-memory safety allocation divided by concurrency. If
memory detection is unavailable, workers share a bounded 2 GiB fallback.

CAD tessellation and STL streaming inside a mesh worker use its existing process,
budget and deadline. CAD output capacity belongs to the supervising parent,
which releases it on every exit; disposable workers never open the application
database for output admission. The parent owns the temporary directory and removes it
after killing and reaping the worker group. Linux supervisors adopt and reap
descendants from their own admission without waiting on another worker. Linux parent-death protection kills
an abandoned worker tree. Its stdlib guardian also removes abandoned temporary
output after checking the directory identity; a replacement is preserved. Unsupported hard-limit platforms fail closed.

Keep framework, storage and job infrastructure out of the core geometry library.
The admission and bootstrap are application responsibilities.

Native STEP GLB conversion uses serial OpenCASCADE tessellation. Its own parallel
thread pool ignores OMP_NUM_THREADS and can exhaust address space through thread
stacks under small budgets; serial execution keeps it inside the admitted worker.
B-rep conversion propagates allocation failures to the resource-limit outcome.

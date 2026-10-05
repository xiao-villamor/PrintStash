# Local native resource admission

Status: Accepted; focused mixed-source load, recovery and PID-namespace qualification passed.

## Context

A semaphore in each process permits the API and worker to spend the same memory
budget independently. Dividing every mesh's ceiling by the configured worker
count instead makes valid large inputs fail when parallelism is increased.
Materializing sources before admission also leaves an unbounded disk window.

## Decision

Use local file-description locks for separate native and source-preparation
pools. Bootstrap binds pools explicitly under the configured data root. Native
credits use stable directories across reboots. Kernel locks disappear at reboot;
closed records are recovered before reuse, and prepared disk copies are removed
before their credits become available. All processes sharing that resource domain must use the
same root and local filesystem with working flock semantics. This is process
coordination, independent of the durable Job engine and the application database.
It introduces no required queue service, network coordinator or geometry-core
infrastructure dependency.

Native claims reserve slots and estimated whole-pipeline bytes together. Header
preflight is bounded and unknown complexity claims the whole pool. Capacity
changes drain old active claims; registration order governs FIFO admission.
This deliberately trades some utilization for progress of large jobs. A queue
wait retains its Job attempt rather than consuming another production attempt.

Warm ONNX models have a separate process-shared residency ledger under
`runtime/inference-models`. Its default partition is 25% of detected physical
memory, separate from geometry's effective 50% partition. Their sum must stay
below 100%; unknown physical capacity refuses model admission. Each resident
claims one configured model slot and at most its configured virtual-memory
ceiling (1024 MiB by default). The parent transfers only the model descriptor;
prepared Artifact descriptors never enter the cached worker lifetime.

A resident uses the same pre-import guarded bootstrap as mesh work. The guardian
retains credit until the process tree exits. An idle model retires when the
residency ledger has a queued claimant, including a model protected from ordinary
cache expiration. Warm requests otherwise reuse their existing model. Residency
slots bound retained processes; each model's existing thread configuration bounds
its execution threads. They do not form a unified global CPU scheduler.

Cold model loads publish a pending identity before releasing the worker-pool
condition. Native admission, process startup and process-tree cleanup run outside
that condition, so another request can honor cancellation or its deadline.
Pending loads and retiring children still count toward capacity. Pool shutdown
invalidates pending generations; a late child is reaped instead of cached, and
model-directory eviction refuses while its load or cleanup remains pending.
A cleanup failure retains that ownership for an explicit retry; cleanup still
attempts the remaining selected children before propagating the original error.

All API and worker processes in this resource domain must use the same memory
partitions, model ceiling and resident count. Changing either partition requires
stopping every process, updating the shared profile, then restarting together.
Rolling processes with different partitions can overcommit physical memory and
are outside this contract. Geometry slot/claim capacity changes within an
unchanged partition continue to drain existing claims before admission.

Source batches reserve their combined byte window first, then take an I/O slot
only during materialization, then acquire native resources. No materialization
starts while holding native resources. Verification reserves both sources at
once, avoiding a pair of workers each holding half of their needed input window.
Actual disk leases remain storage responsibilities and name the directory used.

A native launcher passes the exact locked descriptors through exec. The stdlib
guardian inherits them before native imports. Linux pidfds establish stable
process identities; credits remain held while death or group cleanup is
uncertain. An exec descendant that closes its inherited descriptor does not
release the guardian's copy. A timeout or heartbeat is never proof of death.
Workers remain in the admission's process group; this mechanism does not claim
to contain native code deliberately escaping its process group or namespace.

Prepared source copies live in a private workspace named by a random reservation
identity. A reclaimer acquires the abandoned ticket's descriptor, removes only
that workspace outside the coordinator lock, verifies the ticket identity, then
unlinks the record. It retains credit while cleanup is uncertain. Persistent
Artifact-cache files remain with their existing owner. Native internal temporary
files keep the bootstrap guardian's existing directory-identity cleanup.

## Consequences and qualification

Admission requires local POSIX flock semantics. It passes both the held descriptor
and the ticket path; inheritance verifies the reopened inode and lock ownership
without depending on procfs. Linux production containment additionally requires
pidfd support. macOS development retains process-group termination and startup
without procfs; Linux guardian and process-tree RSS guarantees do not apply there.
The no-procfs paths are tested on Linux with procfs access denied, not certified
by a run on macOS hardware.
Do not share this ledger over a network filesystem or treat unrelated data roots
as one memory budget. Different containers sharing a root also need the same
resource configuration; their native descendants are observed in their own PID
namespace while file-description locks coordinate the common resource domain.
The ledger and prepared workspaces are runtime state, not backup payloads.

Weights are conservative estimates, not measured allocations. The current
Python 3.14.8 numeric stack measured approximately 754 MB peak virtual memory
for a real 300,000-facet STL, while RSS peaked around 473 MB. Its former 660 MB
claim refused valid measurement under RLIMIT_AS. Known-source weights now use
3000 bytes per face with a 512 MiB startup floor; this source claims 900 MB.

Callers also declare a typed work profile. The following peaks were measured
before reducing raster allocation batches; they remain conservative calibration
inputs rather than claimed peaks after that change. Geometry and PNG/RGB work keep that
coefficient. WebP uses 4000 bytes per face and a 640 MiB floor: a 640×480 cube
measured 548,909,056 bytes of virtual memory, while the 225,706-facet Benchy
measured 714,731,520 bytes without fingerprints. Benchy therefore claims
902,824,000 bytes for WebP. Optional analysis has a 1 GiB minimum; Benchy with
WebP and fingerprints measured 746,389,504 bytes. These are whole-process
estimates, so the startup floor is not added again to the source estimate.
Additional raster workspace contributes 64 bytes per pixel beyond the qualified
640×480 frame, including retained views. Every claim is capped by the available
pool; nested work retains its existing allowance. The estimates describe the
qualified workloads, not a memory guarantee for every input topology.

Qualification checks both measured RSS and virtual-memory peaks against claims. Tree RSS sampling
can miss transient peaks; RLIMIT_AS additionally constrains virtual mappings.
The startup floor was measured on Linux x86-64 and needs continued qualification
on supported images. CPU thread defaults remain one BLAS/OpenMP thread per
worker. A separately measured thread policy may change them later.

Queue telemetry is parent-owned and records wait time independently of startup,
execution and cleanup. It uses bounded outcome labels and carries no source
path or Artifact identity in metric labels.

Focused mixed-size native execution, cancellation, configuration draining,
actual PID namespaces and source-workspace recovery are covered by the acceptance
matrix. The measured mixed-STL workload overlaps two real supervised workers,
checks RSS and peak virtual memory against claims, preserves original bytes and
returns complete geometry plus PNGs. It establishes bounded concurrency, not a
throughput speedup or a general memory calibration for every topology or image.
Raster allocation batches now retain at most 125,000 candidate pixels while the
cumulative work allowance remains unchanged. A real tiny 3MF that failed while
allocating winner normals at 250,000 candidates renders complete PNG and WebP
under the same 512 MiB ceiling. Exact pixel and depth comparisons against the
prior batch size protect coverage and output identity.

Extended soak, coverage and supported-image compatibility remain release-gate
work; the focused qualification does not claim those broader checks.

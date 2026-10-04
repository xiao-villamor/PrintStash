# Local native resource admission

Status: Implementation in progress; integrated load and recovery qualification pending.

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

Weights are conservative estimates, not measured allocations. Tree RSS sampling
can miss transient peaks; RLIMIT_AS additionally constrains virtual mappings.
The startup floor was measured on Linux x86-64 and needs continued qualification
on supported images. CPU thread defaults remain one BLAS/OpenMP thread per
worker. A separately measured thread policy may change them later.

Queue telemetry is parent-owned and records wait time independently of startup,
execution and cleanup. It uses bounded outcome labels and carries no source
path or Artifact identity in metric labels.

Integrated mixed-size load, cancellation, configuration-change, namespace and
source-workspace recovery checks must complete before this implementation is
considered qualified. Ordinary CI remains separate from the deeper release gate.

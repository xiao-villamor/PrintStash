# Verified remote Artifact cache

The cache is disposable local data. It reduces repeated provider downloads; it is
not a backup, offline guarantee, source-health result, or catalogue ownership.
Deleting its dedicated directory while PrintStash is stopped is safe. Restart
creates an empty cache. Removing or replacing it during operation degrades cache
health and uses normal source delivery until restart.

## Consumer and eligibility inventory

`artifact_content.resolve(File)` is the read seam for immutable owned Artifact
bytes. Eligibility requires remote managed storage, a known exact size, and the
SHA-256 of the requested representation. Its key includes representation kind,
version, size, and digest. Display names and provider paths are never cache paths.
A transformed representation must supply its own digest and version; its source
Artifact's digest cannot identify transformed bytes.

| Consumer | Route through the seam / deliberate exclusion |
|---|---|
| Original authenticated and shared downloads | `plan_artifact` selects a leased cached path only after route authorization, conditional response, safe native delivery and true local path checks. |
| Printer upload | `printer_jobs.transfer_artifact` holds `materialize()` through provider upload completion. |
| Derivative production | `derivatives.producers` holds `materialize()` for the whole mesh load or G-code read and supplies the declared Artifact format to its parser. |
| Mesh conversion and embedded G-code extraction | File routes materialize the original; mesh parsing uses its declared format even though the private path ends in `.blob`. |
| User archive export | `library_transfer.create_archive` materializes each owned Artifact while writing its ZIP member. |
| Mounted external libraries | Existing `ArtifactHandle` verification before exposure remains authoritative; these mutable linked sources never enter the managed cache. |
| Native provider downloads | Redirects precede cache lookup and never fill it. |
| Mutable thumbnail/document/taxonomy address-only delivery | No immutable digest/version contract currently exists at these key-only seams; they remain uncached. This is an eligibility limitation, not permission to use the source digest. |
| Ingestion staging, remote transports | These are incoming bytes or transport implementations, not reads of an owned immutable Artifact. |
| Vault audit, ownership/deletion checks | Explicit authoritative reads bypass disposable cache evidence. Cache inspection is a separate bounded observation. |
| Full backup and restore verification | The authoritative storage projection and hashes are read directly. User ZIP export and full backup have different integrity purposes. |

## Local layout and lifetime

The dedicated private root has an enrollment marker, a small SQLite WAL index,
private fill files, and sharded `objects/ab/<representation-key>.blob` paths. Only
an empty or enrolled root is accepted; symlinked/replaced roots, foreign ownership,
and symlinked index/shard paths are rejected. Settings reject overlap with managed
storage, staging, thumbnails, and backups. Directories are private and body files
are mode 0600.

The local index owns durable fill claims, entry metadata, active read leases and
counters. Process incarnation identifiers disambiguate PID reuse. SQLite serializes
selection, admission, publication and eviction across workers on one host. It is
not shared through Postgres or a network filesystem. Neither files nor this index
belong in backup or storage migration.

Fills reserve bytes and slots before provider I/O. Streaming computes SHA-256 and
length without accumulating the body in memory. Publication flushes and fsyncs the
same-filesystem temporary file, creates an atomic **no-replace** hard link, fsyncs
the shard directory, then commits index metadata. A verified temporary download
remains usable if optional publication fails, with its reservation retained through
consumer completion. A crash between publication and index commit leaves an exact
recognizable orphan for startup reconciliation. Unrecognized files are not deleted.

A lease starts before returning a selected path and lasts through consumer or
ASGI response completion, including cancellation before file open. Clear marks
leased entries for deferred removal, revokes active publication, and retains their
accounted bytes. Disable stops admission while retaining existing leases and files.
Startup reconciliation removes proven dead-process claims/leases and exact orphan
temps/publications; it preserves live readers and writers.

## Pressure, delivery and operations

Byte, entry and fill-count bounds include outstanding claims. Admission evicts idle
LRU entries and checks actual filesystem headroom; shared capacity reservations
also apply. A cache-volume capacity refusal can use a separately admitted temporary
volume. A real temporary-volume capacity refusal still fails before downloading.
Waits for another caller's fill are bounded by the configurable timeout. Optional
cache/index failures fall back safely; source-integrity failures remain visible.

Complete proxy GETs may tee into the cache. Premature close, cancellation, short
transfer and digest mismatch never publish an entry. Cold Range requests never
publish partial data. A warm cached Range response retains canonical SHA validators
and If-Range behavior. Native redirects retain priority over a warm cache.

Limits apply live. Shrinking limits starts asynchronous idle eviction; active
leases remain counted until release. Settings distinguish disabling from clearing,
show pending reclamation, poll until maintenance settles, and require restart for
root changes. Environment defaults can be overridden in the database and restored.

Settings and health expose cache availability, occupancy, entries, claims, leases,
hit ratio, bytes saved, completed fills, bypasses, evictions, errors, corruptions,
last verification and safe representation/backend labels. Cache degradation does
not mark authoritative storage unhealthy. Local hit verification checks regular
files, size, inode and timestamp; configurable hash sampling detects modifications
that preserve metadata. Bounded full cache inspections rotate through least recently
verified entries. Integrity failures use a fixed diagnostic event without object
keys, filenames, credentials or provider URLs.

The same pure local cache engine serves SQLite/light and Postgres/full deployments;
Postgres stores only optional administrative policy. No Redis or background queue
service is required.

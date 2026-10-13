# Storage capacity and insights

Settings → Storage insights separates logical library references, external references,
known unique owned objects, staging leases, and backup replicas. Logical bytes count
references. Physical ownership is deduplicated by provider, namespace and object key;
two copies with the same SHA-256 still consume two allocations.

The inventory uses SQL aggregates and the existing ownership census. Opening Settings
or the dashboard never walks an object-store namespace. Recorded object sizes are an
estimate of owned bytes, not a provider's billed usage. Objects without recorded sizes
remain unknown. S3 free space and provider quota remain unknown when the provider does
not supply capacity evidence. Local free space comes from `statvfs` on the actual
filesystem; roots sharing one device share one capacity budget.

The optional adapter `capacity()` result records total, used, available and quota bytes
with its timestamp, method and exact/estimated reliability. Unsupported WebDAV, SFTP
and S3 quota evidence remains unknown. A failed refresh retains the last measurement
as degraded evidence, and concurrent refreshes share one bounded adapter probe. The
interactive inventory, health endpoint and Prometheus scrape path only read persisted
or database-aggregated evidence; none performs a remote namespace walk.

The maintenance tick saves at most one sample per UTC hour. History keeps hourly
evidence for 14 days, then one sample per UTC day, and expires evidence after 366 days.
Each sample separates live originals, trash, derived/cache and backups. The forecast
requires seven distinct daily samples spanning at least seven days and positive,
stable growth. It uses the median daily change, withholds estimates around large
outliers, reports its sample window/confidence, records prediction error without
private labels, and never authorizes an allocation.

The latest completed Vault audit contributes only its run id, completion time and
aggregate unclaimed-object count. Object names and keys are not copied into inventory,
health or metrics. Collection and Model drilldowns use the normal live-resource access
scope and fixed pagination limits; Vault-wide operator metrics never use their names as
labels.

## Admission policy

`VAULT_STORAGE_MIN_FREE_BYTES` defaults to 1 GiB. The configuration API accepts a
`storage_min_free_bytes` database override; `-1` resets it to the environment/default.
Existing upload type/byte limits, staging limits and per-user counts still apply.

Heavy operations reserve their peak additional allocations before writing. Claims on
one volume are summed and admitted under a database lock shared by all workers.
Reservations are durable on SQLite and PostgreSQL. Workflows renew before increasing
an estimate, and release after their final allocation. A rejected request returns
HTTP 507 with `storage_capacity_exceeded`; inaccessible or replaced volume evidence
also fails closed.

A reservation is a budget claim, never a deletion receipt. An expired claim remains
charged while its owner may still write. Workflow reconciliation can release a claim
only after proving the owner is terminal. On Linux, expired claims with persisted
host/boot/PID/start-time evidence are reclaimed after their process is demonstrably
gone. Foreign-host, live-process and unknown identities remain charged.

The shared interface is `CapacityManager(session_factory)` with `reserve`, `hold`,
`renew`, `release`, `serialize_admission(session)` and reconciliation operations in `app.modules.storage.capacity`.
Use `CapacityResource.for_path` for the actual staging/cache/output filesystem and
`for_quota` for a provider quota domain. Same-volume resources are added, so callers
must describe peak overlap of input, output and rollback bytes, without counting a
shared object as multiple allocations. Unknown provider capacity produces a warning;
it never grants additional local staging space.

## Cleanup

The **Clean up expired staging** and **Clear derived cache** actions require explicit
confirmation and are audited. Staging cleanup calls the existing staging owner: only
expired files whose device/inode/ctime/size still match their receipts can be removed.
Derived-cache cleanup queues exact, receipt-verified `stl_cache` objects through the
durable deletion outbox. Replaced or uncertain files stay in place and remain charged.
Both actions refresh the saved inventory. Thumbnails, Library artifacts, trash,
backups and other authoritative data are never automatically deleted by this policy;
their existing owner-specific Settings actions remain the only route.

## Percentage headroom and durable work

`STORAGE_MIN_FREE_PERCENT` (0–100, default 0) reserves a percentage of each
measured filesystem or quota domain. Admission uses the larger of that amount
and the configured minimum free bytes, once per domain. Unknown provider quota
remains a warning; an unavailable local measurement prevents unsafe admission.
A capacity rejection includes numeric required, available, reserved and headroom
bytes plus a refresh hint. Neither the response nor telemetry needs object keys.

Resumable work uses `CapacityManager.reserve(..., durable=True)`. Its estimate
survives the creator process and automatic expiry reconciliation. The workflow
owner releases it only after completing or safely cleaning its surviving bytes.
Older upload, PostgreSQL restore and Vault migration claims are conservatively
retained too. Process-scoped scratch work still reconciles after a proven exit.
Reservations remain budget evidence and never authorize storage deletion.

## Allocation census

Every application workflow that can create a substantial local or Vault allocation
uses `CapacityManager` directly or enters through the guarded Artifact materializer.
Helpers listed as inherited must not add an independent reservation because their
owning workflow already reserves the combined peak.

| Workflow | Admission owner | Recheck / release boundary |
|---|---|---|
| Browser/API upload and capture | upload/inbox owner | Before body staging; released after publication or receipt-safe cleanup |
| URL/library import and Artifact publication | importer/ingestion owner | Before download and immediately before the final Vault allocation |
| Archive export/import | library-transfer owner | Before archive creation/extraction; export rechecks its measured census |
| Backup creation | backup creation owner | Before snapshot/archive work and again after the snapshot census; publication helpers inherit the claim |
| Backup upload/download/adoption | backup transfer owner | Before local staging or consuming a remote body; immutable-identity validation remains unchanged |
| Backup restore | restore owner | Remote download is admitted first, then restore is admitted again after the archive member census; rollback and publication are included |
| Vault migration | migration owner | Durable destination plus transfer staging claim; released only after terminal reconciliation |
| Thumbnail/render/conversion work | thumbnail owner | Before source materialization and derivative publication; renderer subprocess helpers inherit the claim |
| Printer-side external capture | printer hub | Before bounded printer download and Vault publication |
| Other external/remote Artifact materialization | `ArtifactHandle` | Before creating the verified local copy; the claim follows stream/path lifetime |
| Expired staging cleanup | staging receipt owner | No admission needed: it only removes exact expired receipt identities after confirmation |

Provider streaming reads, direct local-path reads, inventory SQL aggregation, quick
Vault audit enumeration, backup catalogue listing, health and metrics are read-only and
make no application-sized local allocation. Small configuration probes use fixed-size
temporary files and are not storage workflows. Remote-adapter temporary publication
buffers, backup snapshot/publication helpers and mesh worker files execute inside the
owning workflow's peak claim.

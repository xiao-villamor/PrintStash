# Move Vault storage

The **Settings → Storage → Move Vault storage** workflow copies the existing
Vault, verifies its contents, and switches its active storage configuration.
Changing a location is a migration; editing credentials for the same location
does not copy data. Only an administrator can operate the migration workflow.

## Before starting

- Create a new backup from this installation. Preflight verifies the archive,
  its contents, age, compatibility and exact Vault namespace. An older archive
  without namespace evidence cannot satisfy this prerequisite. The same archive
  is checked again before activation; keep it available and unchanged.
- Run on the supported single-process, single-worker deployment. The mutation
  drain and read-generation leases are process-local, not a distributed lock.
- Prepare dedicated, empty destination roots. Local directories must already
  exist, be writable, and not overlap the source, each other, staging, backups
  or the Artifact cache. Do not select an existing external library directory.
- The destination must provide verified create-only publication and exact
  ownership receipts. Local filesystems and versioned S3-compatible storage are
  exercised by the migration contracts. A provider listed in the selector is
  not necessarily eligible: capability and health checks decide admission.
- Budget space for the complete owned Vault plus online additions. Remote
  transfers can also need local staging space for concurrent destination
  spools. Unknown provider capacity is shown explicitly; a successful preflight
  is not a guarantee that capacity will remain available throughout the copy.

The inventory includes live and trashed Artifacts, documents, embedded images,
derived objects, provenance covers and published capture/staging objects.
External linked files are excluded. Local upload chunks remain with their
upload owner. Incomplete provider-native uploads are fenced at activation and
must be restarted; their original provider is retained for cleanup. Completed
native staging objects are copied with their ownership receipts.

## Copy and activate

1. Select the destination and exact backup source. Choose retention days,
   concurrent copies and an optional aggregate transfer bandwidth limit.
2. Select **Check migration plan**. Preflight checks source health with a Quick
   audit, destination identity and isolation, collisions, limits and capacity.
   The approval digest is configuration-bound and expires; rerun a stale plan.
3. Start the verified copy. Reads and ordinary ingestion continue on the source.
   Pause stops further transfer at bounded checkpoints. Resume rechecks stored
   receipts and content before skipping already-copied objects.
4. When ready, confirm the final switch. Mutating requests are temporarily
   refused with HTTP 503 and `Retry-After: 5`; admitted work is drained. A final
   census copies the online delta and verifies every final object before the
   catalogue, configuration and activation epoch commit together.

Hard purge, destructive GC, restore and location/configuration changes are
suspended while the copy is retained. Downloads already in progress retain
their original storage generation. New reads use the destination only after
activation; failed destination reads never silently fall back to source bytes.

The transfer limit applies to copying payloads. Hash verification, backup
verification and provider probes add I/O beyond that limit. Copying is bounded
by the selected concurrency; activation and source/destination verification can
take significant time on large or high-latency Vaults. Progress reports expose
object and byte counts, skipped objects, delta size, recent errors, throughput
and activity. HTTP and provider timeouts may leave a durable operation running;
refresh its state before attempting another action.

## Restart and recovery

The external activation journal is stored under
`<staging_dir>/vault-migration/activation.jsonl`. Keep the staging directory,
database, encryption key and both provider configurations available across
restarts. Do not edit the journal, reconfigure roots by hand or delete copied
objects to force recovery.

Startup inspects the journal before ordinary mutations. Incomplete or malformed
evidence leaves the instance in maintenance. An administrator can sign in and
use **Recover migration** in the existing workflow:

- If the source configuration and source epoch are still committed, recovery
  leaves copying paused. Explicitly resume to revalidate receipts and continue.
- If the activation intent, manifest, configuration and database epoch prove
  the destination is committed, recovery rebinds the destination and checks its
  health and owned contents. Later legitimate changes use the current catalogue,
  not the obsolete activation snapshot. A post-activation audit is recorded.
- Conflicting or missing evidence remains in maintenance. Preserve the database
  and journal, inspect the reported error and resolve provider availability or
  configuration issues before retrying. Neither side is guessed authoritative.

There is no automatic rollback after activation, even before the first
destination write. The durable first-write marker is conservatively recorded
before a write-capable operation is admitted. To move back, perform a new
verified migration in the opposite direction; switching the old configuration
back can discard newer data.

## Retained source and cleanup

Activation does not delete the source. After the selected grace period:

1. Run the migration's **Full audit** on the active destination.
2. Create a **new destination backup after activation**. The preflight backup
   cannot authorize source cleanup.
3. Select that exact backup and confirm source cleanup for this migration ID.

Cleanup waits for old-generation readers and retained native-upload cleanup.
It deletes only source objects whose stored creation receipt and SHA-256 still
match, recording every deletion in the audit log. Replaced, unowned or otherwise
unproven bytes are retained and reported; directory contents are never blanket
deleted. A discarded candidate is also cleaned only by exact recorded receipts.

Alternatively, **Keep source indefinitely** records retention without deleting
bytes. **Remove source credentials / manual cleanup** records the manual outcome
and drops the migration's stored source configuration; it does not erase the
source. Save any configuration needed for later manual work before choosing it.

Migration history, a credential-free JSON report and configured notification
channels expose the outcome. Detailed health includes a bounded
`vault_migration` component. Cleanup is always a separate, explicit operation.

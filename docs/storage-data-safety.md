# Storage Data Safety And Garbage Collection

PrintStash treats catalog state and stored bytes as separate safety domains.
Catalog rows can be restored from Trash. Physical bytes require stronger proof
because deleting the wrong path or object version is not reversible.

## The Failure Class This Design Prevents

A storage collector must not infer ownership by walking a directory and asking
whether the current database appears to reference each path. That approach can
miss a legitimate reference stored in another column or table, misinterpret a
partially restored database, or see an empty replacement mount. The result can
be deletion of valid user data.

PrintStash 0.13 therefore follows these rules:

- storage is never swept by pathname or by an "unreferenced" listing
- every managed object is published behind a durable ownership intent
- linked Library-source bytes are never owned and never deleted by PrintStash
- candidate discovery is non-destructive
- automatic expiry cannot authorize itself
- the active provider, restore history, candidate rows and backup are checked
  again at each destructive boundary
- uncertain storage cleanup is retained as blocked or pending work, not treated
  as permission to delete by key

The read side follows the same principle. `ArtifactContent` resolves database
ownership first, pins mounted files against replacement and checks remote object
identity around a materialized read.

## Manual Delete And Automatic Expiry Are Different

An administrator can permanently delete one explicitly selected resource
through its existing manual action. Storage capability checks and the ownership
ledger still apply, and a provider that cannot prove the object may retain bytes
and report cleanup as blocked.

Deleting a Multipart Model or removing/replacing its uploaded cover retires the
cover reference. On Guarded storage, PrintStash first checks the committed
ownership record, size and content hash. For local storage (including FUSE),
the durable cleanup intent moves the object into a private, same-directory
quarantine, verifies its identity and hash again, then removes that exact object.
This also reclaims owned model files and thumbnails after a confirmed purge.
Changed bytes are retained; I/O failures remain retryable. Remote adapters that
cannot perform safe conditional reclamation retain the bytes as blocked cleanup.

Older guarded intents blocked solely because physical deletion was unsupported
are retried on the next cleanup or storage verification with the same recorded
ownership evidence. A purge never deletes
Multipart member Models or external-library originals. Missing or changed
ownership evidence blocks the operation with storage guidance. Untracked files
are not swept by walking the storage directories; use the storage inspection
and GC preview to review retained objects before any broader cleanup.

Retention expiry is broader and runs without a person selecting each row. It
uses the GC plan protocol below. The hourly coordinator may create a preview,
wait, finalize an already approved plan after quarantine, or resume a durable
storage outbox. It never approves a plan.

## GC State Machine

```text
expired Trash rows
       |
       v
PREVIEW --abort--> ABORTED
   |
   | exact digest + administrator approval
   | Verified active storage
   | independent verified S3 backup <= 24 h old
   v
QUARANTINED --abort--> ABORTED
   |
   | deadline reached; revalidate every proof
   v
FINALIZING --outbox retry--> COMPLETED
   |
   +-----------------------> BLOCKED
```

Only one active plan can hold the database lease. A preview contains at most:

- 25 resources, further capped to 1 percent of the Model count per plan
- 100 explicitly derived storage keys
- 1 GiB of primary Artifact bytes

The digest binds the retention cutoff, provider identity, restore generation,
resource ids, Trash timestamps, key counts and byte counts. The approval API
accepts only that exact 64-character digest. A restored, edited or concurrently
purged candidate invalidates the plan.

Expired printers participate in the same preview, backup, approval and quarantine
protocol. Finalization removes their tools, material slots, permissions,
maintenance records and remote-file inventory. Print jobs and notification
history remain, with the deleted printer reference detached; saved print-job
names are retained. Removing inventory never sends a command to the physical
printer or deletes its remote files.

## Backup Gate

Approval requires a backup witness with all of these properties:

- created no more than 24 hours ago
- stored on S3, including a compatible provider
- provider identity different from the active Vault storage provider
- exact source reference and archive SHA-256 recorded
- full verification valid
- application version compatibility valid

The backup is verified again at finalization. Losing the archive, changing its
source identity or changing active storage blocks deletion. A backup in another
prefix of the same provider is not an independent failure domain.

Automatic physical GC requires Verified active storage and a fresh, fully
verified, application-compatible backup on an independent S3 provider, for both
SQLite and PostgreSQL. PostgreSQL archives contain a portable SQLite-format
database snapshot. Keep the same witness checks: the archive must be no more than
24 hours old, its S3 provider identity must differ from active Vault storage,
and the exact source, archive digest, and full verification must still match at
finalization.

## Quarantine And Finalization

The default quarantine is seven days. Configure it with
`VAULT_GC_QUARANTINE_DAYS`; lowering it reduces the recovery window. During
quarantine, restore or abort the plan if the preview is wrong.

At finalization PrintStash rechecks:

- immutable plan digest
- active provider identity and Verified capability tier
- restore-generation hash
- every candidate's Trash timestamp and purge state
- exact backup source, provider, digest and verification
- quarantine deadline

Catalog deletion creates durable storage-delete intents. If processing stops,
the next hourly coordinator resumes them. A pending provider response keeps the
plan in `FINALIZING`. A failed proof moves it to `BLOCKED` and releases the
active-plan lease so an operator can investigate. Neither state authorizes a
generic object sweep.

## Operator Workflow

1. Create and verify an independent S3 backup.
2. Open **Settings > Trash** and select **Review expired**.
3. Inspect the count, key estimate, bytes, cutoff, provider and every item.
4. Copy the displayed digest into the approval field. Do not approve if any
   candidate is unexpected.
5. Confirm the plan enters `QUARANTINED` and record its deadline.
6. Restore or abort during the quarantine window if necessary.
7. After the deadline, finalize. Check that the state is `COMPLETED`, not only
   that catalog rows disappeared.

For API automation, use:

```text
POST /api/v1/admin/gc
GET  /api/v1/admin/gc
GET  /api/v1/admin/gc/{run_id}
POST /api/v1/admin/gc/{run_id}/approve   {"digest":"<exact digest>"}
POST /api/v1/admin/gc/{run_id}/abort
POST /api/v1/admin/gc/{run_id}/finalize
```

All routes require a superuser.

## Recovery Runbook

If a plan is wrong while in `PREVIEW` or `QUARANTINED`, abort it and restore any
affected rows from Trash. No physical deletion has occurred.

If a plan remains `FINALIZING`, stop manual storage changes. Inspect the durable
storage-delete intents and provider health, then let the hourly coordinator
retry. Do not delete the keys by hand.

If a plan is `BLOCKED`, preserve the database, active storage and witness backup
before investigation. A blocked plan means PrintStash refused to prove one of
its assumptions. It is not evidence that the remaining object is orphaned.

If valid bytes are already missing, stop all writers and follow
[Disaster Recovery](./disaster-recovery.md). Restore the database and managed
storage as one matched set. Library-source files require their own operator
backup.

## OpenDAL S3 backup witnesses

A verified backup in an OpenDAL S3 connection can satisfy GC's independent-backup requirement. PrintStash resolves the exact source reference, committed ownership, archive digest and current target before verification. The active Vault must still have Verified storage capabilities.

Custom S3 targets require an administrator-declared failure domain bound to the current target identity. Different profiles, prefixes or credentials do not establish independence, and a declaration cannot override known shared storage. Approval records the evidence; finalization verifies the same archive and rechecks the evidence after quarantine. A removed profile, edited target, changed declaration, changed archive or incompatible backup blocks finalization and preserves the candidates.

This eligibility does not grant backup deletion, promote the destination's maturity, or enable witnesses from other remote transports.

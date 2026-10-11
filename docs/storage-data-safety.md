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

## Release Evidence

The implementation is covered at integration and end-to-end boundaries for:

- preview without deletion
- bounded selection and the one-percent cap
- single active-plan lease
- refusal without an independent verified backup
- provider and restore drift refusal
- quarantine enforcement
- changed-candidate refusal
- resumable finalization and blocked storage cleanup
- restore-versus-purge conflict handling

Provider deletion semantics remain separate contract tests. See
[Provider Support](./provider-support.md#storage-and-library-source-compatibility)
and [Release Validation](./release-validation.md).


### OpenDAL S3 backup witnesses

A verified backup in an OpenDAL S3 connection can satisfy GC's independent-backup requirement. PrintStash resolves the exact source reference, committed ownership, archive digest and current target before verification. The active Vault must still have Verified storage capabilities.

Custom S3 targets require an administrator-declared failure domain bound to the current target identity. Different profiles, prefixes or credentials do not establish independence, and a declaration cannot override known shared storage. Approval records the evidence; finalization verifies the same archive and rechecks the evidence after quarantine. A removed profile, edited target, changed declaration, changed archive or incompatible backup blocks finalization and preserves the candidates.

This eligibility does not grant backup deletion, promote the destination's maturity, or enable witnesses from other remote transports.


## Publication, Adoption And Retirement

Byte preparation commits a `PENDING` ownership reservation with an immutable
reservation generation and exact creation receipt. Preparation does not grant
permission to attach a domain pointer. Guarded producers first lock their Job
execution and domain output, update the pointer, then adopt the prepared receipt
in the same transaction. A rolled-back domain transaction leaves its durable
pending receipt available to the orphan owner.

Adopters, reservers, collectors and cleanup use one persistent SQL locator
anchor, ordered by backend, namespace and key. Domain authority and output rows
are locked before these anchors; receipt verification and provider I/O happen
before the final SQL attachment phase or after its commit. The anchor also
arbitrates the first insertion when no ownership row exists.

Retirement changes a specific reservation generation to `RETIRING` and commits
its exact delete intent before cleanup I/O. A retired reservation cannot be
revived by a late creator or adopter. A new physical generation may reuse the
same canonical locator with a distinct reservation. Incomplete retired receipts
keep their recovery evidence and a future recovery date; uncertain evidence is
deferred, and bytes of an active successor are never inferred to be the old
creator's output.

Completed delete intents remain exact-receipt revocations. Locator anchors and
retired ownership history are retained as well: the current implementation has
no age-based prune for this authority. These rows grow with publication and
retirement history. A future compactor must prove that no late reservation,
receipt, restore or legacy adopter can reintroduce a removed revocation; expiry
alone is insufficient. Retired history is excluded from current inventory and
backup-retention counts.

Local adoption tokens identify a physical generation (device, inode, ctime,
size and digest), so identical bytes in a replacement inode have different
authority. Historical local receipts remain valid only for their exact recorded
physical identity.

Remote receipts with an immutable version ID identify that physical version
even when a legacy adopter supplies a different logical token. Exact retirement
revokes the version itself. OpenDAL recovery may reconstruct a lost logical
token only after pinning an immutable version and validating the archive digest;
without that physical authority, the reservation stays pending with an explicit
missing-evidence outcome instead of manufacturing an empty receipt.

Delayed S3 cleanup requires an immutable version ID. ETags may repeat after
replacement and therefore cannot authorize delayed deletion. Unsupported
unversioned cleanup remains explicitly blocked and is not continuously retried.
Manual backup deletion reports `backup_exact_delete_unsupported` and preserves
its archive and catalog proof. Enable bucket versioning before archive creation;
enabling it later does not retrofit older receipts. Version deletion targets
only the recorded version even if a newer version has the same content ETag.

Import completion prepares source-cover candidates at private immutable keys.
It inserts or switches the visible source-cover pointer only after the Job and
Inbox authority fence, then adopts the new receipt and retires the old receipt
in the same transaction. A retired import can neither expose its first cover
nor overwrite an existing cover's bytes.

Intentional cover replacement through the explicit API remains supported; failure
compensation first commits exact revocation and yields to any owner that already
adopted the physical generation.

Restore is an exclusive maintenance operation: its durable restore journal owns
staged blob generations and offline database reconstruction. It does not share a
live publication transaction with ingestion. Vault migration stages objects in
its journal and adopts destination receipts only after domain/config changes in
its activation transaction.


## Disposable ingestion workspaces

Downloads, archive entries and local copies use private flat workspaces with a
SQL receipt written before filesystem creation. The receipt records the owning
Job execution, directory and lock identities, output identity and durable
capacity claim. A process lock prevents cleanup while the writer is alive.
Payload is admitted only after physical identities have been committed and
capacity reserved. An uncertain empty creation is retained for inspection; it
has no payload allocation or capacity credit.

Closing an entry releases its exact workspace before the next entry starts.
An unlink or SQL failure keeps its receipt and capacity claim and stops further
batch admission. A new Job execution recovers the prior execution's windows
before admitting another input. Cleanup also runs as an indexed, bounded
`ingestion.scratch_cleanup` Job source after interruption or restart. A live
input staging lease protects the output even if the transfer acknowledgement
was interrupted.

Recovery first checks the physical identities and private ownership marker,
then quarantines and removes the recorded directory. A replaced directory,
lock or sealed output is preserved. SQL retirement follows physical cleanup;
a failed acknowledgement can be retried without guessing ownership. Downgrading
past the scratch migration is blocked until all receipts have been reconciled.

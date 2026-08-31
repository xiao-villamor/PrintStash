# ADR-0004: Read-only remote library sources and witnessed automatic GC

- **Status**: Accepted and implemented
- **Date**: 2026-08-31
- **Deciders**: maintainers

## Context

ADR-0003 separates managed storage by measured capability rather than provider
name. Two adjacent requirements still lacked a durable boundary:

1. Users want existing NAS and cloud files discovered without copying them into
   managed storage or saturating the network.
2. Retention expiry must reclaim managed bytes without repeating the failure
   class where an ownership census omitted a legitimate reference and a garbage
   collector deleted valid data.

A `StorageBackend` owns keys and therefore needs safe publication and deletion.
A Library source does not own bytes and must never inherit that delete authority.
Similarly, a scheduled process can discover expired rows but must not turn its
own observation into irreversible authority.

## Decision

### LibrarySource is a read-only seam

S3, WebDAV and SFTP discovery use `LibrarySource`, not `StorageBackend`.
Connections are reusable and encrypted, source keys are durable, listings are
paged, and content is materialized behind stability checks. Remote write-back
is disabled. Mounted folders retain the existing create-only write-back option
because their descriptor and root-marker guarantees are different.

Remote epochs persist their cursor and observed set. A partial epoch never
interprets absence. Scan work is bounded by page, byte, rate and wall-clock
budgets, and provider errors apply backoff. Tombstones suppress rediscovery
after a user trashes a linked Artifact until Restore or Rediscover clears them.

### ArtifactContent owns every Artifact read

Callers no longer branch on `File.path`. `ArtifactContent` chooses managed,
mounted or remote content and provides either a checked stream/materialized
path. This prevents preview, export, printer and repair code from silently
assuming that an opaque storage key is a local filesystem path.

### Automatic GC is a witnessed state machine

Automatic expiry creates only a bounded, durable preview. Approval is a
separate administrator action over an exact digest and requires:

- Verified active storage
- a fully verified, application-compatible S3 backup no more than 24 hours old
- a backup provider identity different from active Vault storage
- a configurable quarantine, seven days by default

Finalization revalidates the candidate rows, provider, restore history and
backup. Physical cleanup is driven only by the ownership outbox. No filesystem
or object-store walk is a source of delete authority.

## Consequences

- A remote source may be slower than a recursive listing because bounded
  paging, pacing and stability checks are intentional.
- Remote source write-back is unavailable even when the underlying protocol
  could accept writes. It can be added only behind a new capability and safety
  decision.
- A missing, empty or partially listed source delays catalog reconciliation
  instead of producing a clean-looking but destructive result.
- Automatic physical GC is unavailable without an independent recent S3
  backup and Verified storage. Some small or PostgreSQL installs will retain
  expired bytes until an operator changes that setup or uses explicit manual
  deletion.
- Quarantine consumes storage for longer, but creates a recovery interval in
  which a mistaken plan can be aborted.
- Compatibility claims are made for tested protocols. NAS appliance
  certification requires a named model/firmware validation log and is not
  inferred from the presence of SMB, NFS, WebDAV, SFTP or S3 in vendor docs.

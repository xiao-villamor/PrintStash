# Disaster Recovery Runbook

Use this when a self-hosted PrintStash install needs to be restored after a bad
upgrade, disk problem, or accidental data loss.

## What A Backup Contains

A PrintStash backup archive contains:

- the database dump or SQLite database content
- stored model/G-code files
- thumbnails
- a manifest with backup id, timestamp, and app version

Backup archives are built once, then published to each selected destination.
Legacy S3/R2 settings remain readable for upgrades but are no longer configured
in the UI. Reusable remote connections can receive independent replicas on S3,
WebDAV, SFTP or Google Drive. A failed destination does not discard successful
copies or prevent another selected destination from receiving the archive.

Under **Settings → Backup**, administrators can enable one automatic backup per
UTC day, choose its time, and independently select each remote connection for
manual and automatic replicas. Automatic backups are disabled by default. The
local destination is independently selectable for each mode. At least one
destination must be selected; remote-only backups do not retain a local archive.

Connections can be edited under **Settings → Remote storage**. Blank stored
credentials are retained; entering a replacement changes only that credential.
Changing the endpoint, account, bucket or folder is blocked while a Library
source, owned backup or saved backup result depends on the target. Create a
separate connection for another location. Google Drive account credentials
cannot be replaced while dependencies exist because account identity is not yet
verifiable.

Add those connections under **Settings → Remote storage** and allow **Backup
replicas** (or both uses). Create, upload, retain, and restore archives under
**Settings → Backup**. Backup archives use the reserved
`printstash-backups` folder; do not select that path as a Library source. Remote
restores are accepted only when the saved ownership receipt, provider profile,
object identity, size, and SHA-256 still agree. Google Drive is beta: restore is
hash-verified, but automatic retention is disabled because the provider cannot
offer the immutable delete identity PrintStash requires.

The built-in create/restore flow currently requires file-backed SQLite. A
PostgreSQL deployment must use an operator-managed `pg_dump`/restore workflow
and back up the configured object/local storage separately. Query
`GET /api/v1/backups/capabilities/database` to detect support before offering
the built-in action.

## Run History And Exact Replica Retry

**Settings → Backup** shows each run as running, completed, partial or failed.
Completed means every selected destination committed its publication, including
any connection that was invalid when the run began. A publication timestamp is
not a verification timestamp: last verified success advances only after the
exact archive passes digest and compatibility checks. Operational health reports
the latest outcome and last verified time.

For a failed destination, **Retry** reads a live surviving owned copy, verifies
its exact digest, and sends that same archive to the original saved target. It
does not build a fresh backup or substitute a cached restore download. Edited,
removed or unavailable targets cannot redirect a retry. If no verified copy
survives, create a new backup. Google Drive retries remain unavailable until the
authenticated account has a verifiable target identity; existing archives remain
listable and recoverable.

After an interruption, run history exposes the unfinished result. Retrying first
reconciles the exact publication ownership record; a write committed before the
response was lost can be recovered without a second publication. Retry attempts
retain their outcome and the result supplying their verified bytes.

Administrator APIs are `GET /api/v1/backups/runs`,
`GET /api/v1/backups/runs/{run_id}` and
`POST /api/v1/backups/runs/destinations/{result_id}/retry`.
Create retains its successful response fields and HTTP 202, adding `run_id`,
`outcome` and `destination_results`. An all-destinations-failed HTTP 502 response
retains its error detail and includes `run_id` for inspection.

## Create A Backup Before Risky Work

Via API:

```bash
curl -X POST \
  -H "Authorization: Bearer <admin-token>" \
  http://localhost:8000/api/v1/backups
```

Via UI: open **Settings → Backup**, then create a backup before upgrading.

Automatic GC approval has a stricter requirement than an ordinary recovery
backup. Its witness must be no more than 24 hours old, fully verified,
application-compatible, stored on S3, and use a provider identity different
from active Vault storage. A second prefix on the same provider is not
independent. See [Storage Data Safety](./storage-data-safety.md).

## Restore From A Backup

1. Stop slicer hooks and any automation that uploads files.
2. Stop the frontend/API containers.
3. Keep a copy of the current broken volume or data directory if disk space
   allows.
4. Start only the API container with the same storage settings used by the
   backup.
5. If more than one backup location has the same backup id, first list exact
   sources and choose the intended opaque `source_ref` after checking its
   namespace, key, size, digest, and provider identity:

```bash
curl \
  -H "Authorization: Bearer <admin-token>" \
  http://localhost:8000/api/v1/backups/sources
```

   Older validated local, legacy S3, or OpenDAL archives that are not yet owned
   remain untouched until an administrator explicitly adopts the exact candidate
   in **Settings → Backup**. Recreate the matching connection under **Remote
   storage** first if this is a fresh installation. PrintStash re-downloads the
   archive and verifies the exact connection identity and SHA-256 before adoption.
   Never infer a source from backup id alone.

   If the archive is on another machine or provider that is not configured as a
   remote connection, download it yourself and use **Upload backup archive** in
   **Settings → Backup**. Uploaded archives are size-bounded and fully validated
   before they are registered or offered for restore.

6. Restore the chosen backup. Pass `source_ref` when the id is present in more
   than one location:

```bash
curl -X POST \
  -H "Authorization: Bearer <admin-token>" \
  "http://localhost:8000/api/v1/backups/<backup-id>/restore?source_ref=<opaque-source-ref>"
```

7. Restart the full stack.
8. Run the smoke checks from [UPGRADE.md](../UPGRADE.md).

## If The UI/API Cannot Start

- For Docker, inspect named volumes with `docker volume ls`.
- Copy the SQLite DB and `/data/files`, `/data/thumbs`, and `/data/backups`
  volumes before experimenting.
- Restore onto a fresh Compose stack using the known-good backup archive.
- Prefer restoring a backup over manual database edits.

## If A GC Plan Is Active

- `preview` or `quarantined`: abort the plan before restore. No planned physical
  deletion has happened.
- `finalizing`: stop writers and do not delete provider objects by hand. Preserve
  the database and inspect the durable storage-delete outbox.
- `blocked`: treat the failed proof as a safety stop, not as evidence that an
  object is disposable. Preserve active storage and the exact witness backup.

A restore changes the restore-generation proof and invalidates any old GC plan.
After recovery, create a fresh preview from the restored catalog.

Library-source bytes are excluded from a PrintStash backup because PrintStash
does not own them. Restore those from the NAS/object-store backup first, verify
the source path or profile, and only then resume discovery.

## Recovery Targets

- Home installs: restore the latest known-good backup and re-upload any slicer
  exports made after that backup.
- Small farms: restore the backup, then verify each Moonraker/Klipper printer
  status page before sending new jobs.

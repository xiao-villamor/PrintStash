# Disaster Recovery Runbook

Use this when a self-hosted PrintStash install needs to be restored after a bad
upgrade, disk problem, or accidental data loss.

## What A Backup Contains

A PrintStash backup archive contains:

- the database dump or SQLite database content
- stored model/G-code files
- thumbnails
- a manifest with backup id, timestamp, and app version

Backup archives are written and committed locally first. Legacy S3/R2 settings
remain readable for upgrades but are no longer configured in the UI. Reusable
remote connections can replicate each archive independently to S3, WebDAV, SFTP,
or Google Drive; one remote failure never discards the local archive or prevents
another destination from receiving its copy.

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

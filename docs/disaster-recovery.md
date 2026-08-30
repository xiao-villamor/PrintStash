# Disaster Recovery Runbook

Use this when a self-hosted PrintStash install needs to be restored after a bad
upgrade, disk problem, or accidental data loss.

## What A Backup Contains

A PrintStash backup archive contains:

- the database dump or SQLite database content
- stored model/G-code files
- thumbnails
- a manifest with backup id, timestamp, and app version

Backup archives are written locally first. If backup S3/R2 settings are
configured, the archive is also uploaded to the backup bucket.

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

Via UI: open Settings, review backup storage, then create a backup before
upgrading.

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

   Older validated local or S3 archives that are not yet owned remain untouched
   until an administrator explicitly adopts the exact candidate in Settings.
   Never infer a source from backup id alone.

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

## Recovery Targets

- Home installs: restore the latest known-good backup and re-upload any slicer
  exports made after that backup.
- Small farms: restore the backup, then verify each Moonraker/Klipper printer
  status page before sending new jobs.

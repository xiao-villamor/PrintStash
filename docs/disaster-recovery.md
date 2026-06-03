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
5. Restore the backup:

```bash
curl -X POST \
  -H "Authorization: Bearer <admin-token>" \
  http://localhost:8000/api/v1/backups/<backup-id>/restore
```

6. Restart the full stack.
7. Run the smoke checks from [UPGRADE.md](../UPGRADE.md).

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

---
title: Backup & restore
description: What a backup contains, how to make one, and how to recover an install.
---

PrintStash backs up the *whole* install, not just metadata. This is the path you
use to move an install to new hardware, or to recover after a bad upgrade or a
disk going sideways. (For getting just the metadata out — for analysis or
migrating between tools — use the JSON/CSV export in
[the user guide](/PrintStash/guides/user-guide/#6-get-your-data-back-out)
instead.)

## What's in a backup

A backup is a single `tar.gz` archive containing:

- the database (the SQLite file's contents, or a Postgres dump)
- the stored model and G-code blobs
- thumbnails
- a manifest recording the backup id, timestamp, and app version

Archives are written to `VAULT_BACKUP_DIR` (default `/data/backups`) first. If
you've configured the `VAULT_BACKUP_S3_*` variables, the same archive is also
uploaded to your backup bucket — which can be a completely different provider from
your vault storage. A common, sensible setup is vault data on local disk, backups
shipped nightly to R2.

## Make a backup

Always take a fresh one before anything risky — upgrades especially.

From the UI: **Settings → Storage**, review the backup destination, and trigger a
full backup.

From the API:

```bash
curl -X POST \
  -H "Authorization: Bearer <admin-token>" \
  http://localhost:8000/api/v1/backups
```

Old archives are pruned according to `VAULT_BACKUP_RETENTION_DAYS` (default 30;
set `0` to keep them forever).

## Restore

Restoring **replaces** the current database and files with the contents of an
archive, so treat it as a deliberate operation:

1. Stop slicer hooks and any automation that uploads files, so nothing arrives
   mid-restore.
2. Stop the frontend/API containers.
3. If you have the disk space, keep a copy of the current (broken) volume or data
   directory before you touch it.
4. Bring up **only** the API container, using the *same* storage settings the
   backup was made with.
5. Restore:

   ```bash
   curl -X POST \
     -H "Authorization: Bearer <admin-token>" \
     http://localhost:8000/api/v1/backups/<backup-id>/restore
   ```

6. Restart the full stack.
7. Run the smoke checks below.

## When the UI or API won't even start

If the app is too broken to drive the restore through the API:

- For Docker, list your named volumes with `docker volume ls` — you're looking
  for `printstash_data`, `printstash_thumbs`, `printstash_db`, and
  `printstash_backups`.
- Copy the SQLite DB and those data volumes somewhere safe *before* you
  experiment with anything.
- Restore your known-good archive onto a fresh Compose stack rather than trying
  to surgically repair the broken one.
- Prefer restoring a backup over hand-editing the database. Manual DB edits are
  how a recoverable problem becomes an unrecoverable one.

## Smoke checks after a restore

- Sign in at `http://localhost:3000`.
- Hit `http://localhost:8000/api/v1/health` — database, storage, backup, and
  printer-provider components should all report in.
- Confirm a known model appears in search with its metadata intact.
- If you run Moonraker/Klipper, open a printer detail page and check live status.

## Recovery targets

- **Home install:** restore the latest known-good archive, then re-upload (or
  re-push from the Orca hook) any exports made after that backup.
- **Small farm:** restore, then verify each printer's status page before sending
  new jobs — don't queue work onto a printer you haven't re-confirmed.

The matching upgrade steps and rollback expectations live in
[Upgrading](/PrintStash/guides/upgrading/).

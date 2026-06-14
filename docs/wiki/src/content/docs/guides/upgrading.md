---
title: Upgrading
description: Upgrade an existing install safely, with a real rollback path.
---

Upgrades are designed to be boring: pull the new images, run migrations, restart.
The two habits that keep them boring are **taking a backup first** and **keeping
the old image around** until the new one has proven itself.

## Before you upgrade

- Know where your data lives — the Docker volumes or local data directories.
- Take a fresh backup. See [Backup & restore](/PrintStash/guides/backup-and-restore/).
- Briefly stop slicer hooks and scheduled jobs so no uploads land during the
  migration window.
- Keep the previous image tag available so rollback is a real option, not a
  hope.

## Docker Compose upgrade

```bash
docker compose down
docker compose pull
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
```

If you build from source instead of pulling published images:

```bash
docker compose down
docker compose build --pull
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
```

Running migrations as an explicit step (rather than letting the app do it on
boot) means a migration failure stops you *before* the new app starts serving,
which is exactly when you want to know.

### Pinning images

Tagged releases are published to GHCR:

- `ghcr.io/xiao-villamor/printstash-api:<version>`
- `ghcr.io/xiao-villamor/printstash-frontend:<version>`

Pin these in a Compose override when you want repeatable upgrades and don't want
`latest` moving under you.

## Local development upgrade

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
```

Then restart the backend and frontend dev servers.

## SQLite notes

- Fresh installs use `sqlite:////data/db/printstash.sqlite`.
- Some early installs still have `sqlite:////data/db/nexus3d.sqlite` from before
  the rename. If you're upgrading an existing volume, **keep that `VAULT_DB_URL`**
  — don't repoint it, or you'll start against an empty database.
- Always back up the SQLite file before running migrations.
- Never edit the SQLite file while the API container is running.

## Rollback

Schema downgrades are **not** the supported rollback path — backup restore is.
If an upgrade goes wrong:

1. Stop the upgraded containers.
2. Restore the backup you took before upgrading (see
   [Backup & restore](/PrintStash/guides/backup-and-restore/)).
3. Start the *previous* application tag against the restored database and files.

This is the whole reason for taking a backup and keeping the old tag: rollback is
"restore and run the old image," not "reverse the migrations."

## Smoke checks after upgrading

- Sign in at `http://localhost:3000`.
- Hit `http://localhost:8000/api/v1/health` and confirm database, storage,
  backup, and printer-provider components report in.
- Upload a small G-code fixture and confirm it appears in search with parsed
  metadata.
- If you run Moonraker/Klipper, open a printer detail page and verify live
  status.

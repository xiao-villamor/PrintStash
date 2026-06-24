---
title: Upgrading
description: Upgrade an existing install safely, with a real rollback path.
---

Upgrades are designed to be boring: pull the new images, run migrations, restart.
The two habits that keep them boring are **taking a backup first** and **keeping
the old image around** until the new one has proven itself.

## Before you upgrade

- Know where your data lives: the Docker volumes or local data directories.
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

## Troubleshooting migrations

**`No module named alembic.__main__; 'alembic' is a package and cannot be
directly executed`.** Run migrations with the `alembic` console script, not
`python -m alembic` — Alembic ships no runnable `__main__`. The supported command
is the one above:

```bash
docker compose run --rm api uv run alembic upgrade head
```

If you must invoke Python directly, the equivalent is
`python -m alembic.config upgrade head`. Don't replace the Compose `command:`
block with a hand-written `python -m alembic …` line — the shipped command
already does the right thing.

**`table … already exists` when running `alembic upgrade head`.** This shows up
if the app was ever started *without* running migrations first — most commonly
after deleting the Compose `command:` block so the container "just starts." On
first boot the app creates the current schema itself, but that path doesn't
record a migration version, so Alembic later tries to re-create tables that are
already there.

Your data is intact — Alembic simply doesn't know the schema is already current.
The fix is a one-time **stamp**, done *while still on the image version the app
last ran*, which records "this database is at this version" without running any
migration or touching a single row:

```bash
# back up first (Settings → Backups, or POST /api/v1/backups)
docker compose run --rm api uv run alembic stamp head
```

After that, upgrades work normally (`alembic upgrade head`). `stamp` only writes
the version marker; it never creates, alters, or drops tables. If you're unsure
which version built your schema, take a backup and restore-then-replay instead
(see [Rollback](#rollback)).

## Version-specific notes

### 0.7.0 — Notifications & event hooks

- **The schema change is additive and safe by default.** Two Alembic migrations
  add the notification tables and a `notifications_enabled` flag on
  `system_config` (defaulting to off). No existing tables or columns are altered
  or dropped, so the upgrade is a normal `alembic upgrade head`.
- **Notifications start disabled.** Existing installs are not opted in — enable
  the master switch and configure channels under **Settings → Notifications**
  when you're ready. Nobody gets surprise alerts on upgrade.
- **No new required configuration.** All new settings have defaults; you don't
  have to touch your `.env`.
- **Dense meshes now skip thumbnail rendering.** To stop a single high-poly
  model (a multi-million-triangle lattice/gyroid) from OOM-killing a library
  scan, meshes above `VAULT_MESH_MAX_RENDER_TRIANGLES` (2,000,000 by default)
  are no longer loaded for geometry/thumbnails. Such files are still indexed,
  and 3MF still shows its embedded slicer preview. Raise the cap if your host
  has the memory and you want renders for these models.
- **Thumbnails look different.** Smooth shading and a Z-up 3/4 framing change how
  previews look; existing cached thumbnails are kept until rebuilt.

## SQLite notes

- Fresh installs use `sqlite:////data/db/printstash.sqlite`.
- Some early installs still have `sqlite:////data/db/nexus3d.sqlite` from before
  the rename. If you're upgrading an existing volume, **keep that `VAULT_DB_URL`**;
  don't repoint it, or you'll start against an empty database.
- Always back up the SQLite file before running migrations.
- Never edit the SQLite file while the API container is running.

## Rollback

Schema downgrades are **not** the supported rollback path; backup restore is.
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

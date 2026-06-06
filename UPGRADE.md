# PrintStash 0.1 Upgrade Guide

This guide covers upgrading an existing self-hosted install to the 0.1 initial
release. SQLite and local disk remain the recommended default path.

## Before You Upgrade

- Confirm you know where your Docker volumes or local data directories live.
- Create a fresh backup from the UI or API.
- Stop slicer hooks or scheduled jobs briefly so no uploads arrive during the
  migration window.
- Keep the previous image/tag available until the upgraded app has passed smoke
  checks.

## Docker Compose Upgrade

```bash
docker compose down
docker compose pull
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
```

If you build locally instead of pulling an image:

```bash
docker compose down
docker compose build --pull
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
```

Tagged release images are published as:

- `ghcr.io/xiao-villamor/printstash-api:<version>`
- `ghcr.io/xiao-villamor/printstash-frontend:<version>`

Pin those images in a Compose override when you want repeatable upgrades without
building from source.

## Local Development Upgrade

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
```

Then restart the backend and frontend development servers.

## SQLite Notes

- New 0.1 Compose installs use `sqlite:////data/db/printstash.sqlite`.
- Early development installs may still have `sqlite:////data/db/nexus3d.sqlite`;
  keep that `VAULT_DB_URL` if you are upgrading an existing volume rather than
  starting fresh.
- Always back up the SQLite file before running migrations.
- Do not edit the SQLite file directly while the API container is running.

## Rollback Expectations

- Stop the upgraded containers before attempting rollback.
- Restore the backup created before upgrade.
- Start the previous application tag against the restored database/files.
- Schema downgrades are not the supported rollback path; backup restore is.

## Smoke Checks

After the upgrade:

- Open `http://localhost:3000` and sign in.
- Check `http://localhost:8000/api/v1/health`; database, storage, backup, and
  printer provider components should be visible.
- Upload a small G-code fixture.
- Confirm the model appears in search with parsed metadata.
- If using Moonraker/Klipper, open the printer detail page and verify live status.

For recovery details, see [docs/disaster-recovery.md](./docs/disaster-recovery.md).

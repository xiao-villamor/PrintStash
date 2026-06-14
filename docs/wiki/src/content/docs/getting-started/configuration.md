---
title: Configuration
description: The environment variables that control storage, auth, uploads, and backups.
---

Everything is configured through environment variables, normally via the `.env`
file you copied from `.env.example`. Backend settings use the `VAULT_` prefix —
that prefix is historical (the project started life as "Vault") and is kept for
compatibility, so don't read anything into it.

You rarely need to touch most of these. A default install runs on SQLite and
local disk with sensible defaults; the one value you *must* change is the JWT
secret.

:::caution
Change `VAULT_JWT_SECRET` before PrintStash is reachable from anything but
localhost. It signs every auth token — anyone who knows it can mint a valid admin
token. Generate something long and random and treat it as a credential.
:::

## Core

| Variable                            | Default                              | Purpose                                        |
| ----------------------------------- | ------------------------------------ | ---------------------------------------------- |
| `VAULT_JWT_SECRET`                  | `changeme_jwt_secret_please_change`  | Signing secret for auth tokens. **Change it.** |
| `VAULT_JWT_ALGORITHM`               | `HS256`                              | JWT signing algorithm.                          |
| `VAULT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60`                                 | Access-token lifetime, in minutes.             |
| `VAULT_API_KEY`                     | `changeme`                           | Shared key for headless scripts / the Orca hook. |
| `VAULT_CORS_ORIGINS`                | _(empty)_                            | Comma-separated browser origins allowed to call the API. Only needed if the UI is served from a different origin than the API. |
| `VAULT_MAX_UPLOAD_MB`               | `512`                                | Maximum size per uploaded file, in MB. Docker Compose also maps this to the frontend nginx proxy body limit. |
| `VAULT_LOG_LEVEL`                   | `INFO`                               | Backend log verbosity.                         |

## Storage

| Variable                  | Default                                  | Purpose                                  |
| ------------------------- | ---------------------------------------- | ---------------------------------------- |
| `VAULT_STORAGE_BACKEND`   | `local`                                  | `local` disk or `s3` object storage.     |
| `VAULT_DATA_DIR`          | `/data/files`                            | Where model/G-code blobs live (local).   |
| `VAULT_THUMB_DIR`         | `/data/thumbs`                           | Generated thumbnails.                    |
| `VAULT_STAGING_DIR`       | `/data/staging`                          | Scratch space for in-flight uploads. Always local, even on the S3 backend. |
| `VAULT_DB_URL`            | `sqlite:////data/db/printstash.sqlite`   | SQLite by default; point at a Postgres URL to switch. |

### S3 / R2-compatible object storage

Set `VAULT_STORAGE_BACKEND=s3` and supply credentials. This works with AWS S3
and any S3-compatible service — Cloudflare R2, MinIO, Backblaze B2, and so on.
Uploads above the multipart threshold are chunked, and downloads are served as
short-lived presigned URLs so blobs never round-trip through the API process.

| Variable                                | Default       | Purpose                                |
| --------------------------------------- | ------------- | -------------------------------------- |
| `VAULT_S3_BUCKET`                       | _(empty)_     | Bucket name.                           |
| `VAULT_S3_ENDPOINT_URL`                 | _(empty)_     | Custom endpoint (R2 / MinIO / B2).     |
| `VAULT_S3_REGION`                       | `auto`        | Region.                                |
| `VAULT_S3_ACCESS_KEY`                   | _(empty)_     | Access key.                            |
| `VAULT_S3_SECRET_KEY`                   | _(empty)_     | Secret key.                            |
| `VAULT_S3_PRESIGNED_URL_EXPIRE_SECONDS` | `900`         | Lifetime of presigned download URLs.   |
| `VAULT_S3_MULTIPART_THRESHOLD_MB`       | `50`          | Files larger than this use multipart.  |
| `VAULT_S3_LIFECYCLE_EXPIRATION_DAYS`    | `0`           | Object expiration (0 = disabled).      |
| `VAULT_S3_LIFECYCLE_TRANSITION_DAYS`    | `0`           | Days before a storage-class transition.|
| `VAULT_S3_TRANSITION_STORAGE_CLASS`     | `STANDARD_IA` | Storage class to transition into.      |

## Database

SQLite is the default and the best-tested path for a single-user home install —
one file, no extra container, trivial to back up. Reach for Postgres when you're
running multiple users or want concurrent writes to behave better under load:

```bash
docker compose --profile postgres up -d
```

```ini
VAULT_DB_URL=postgresql://printstash:printstash@postgres:5432/printstash
POSTGRES_DB=printstash
POSTGRES_USER=printstash
POSTGRES_PASSWORD=printstash
```

Migrations run automatically on startup against whichever database you point at.
There's a one-time SQLite-to-Postgres migration script if you start on SQLite and
outgrow it later.

## Backups & retention

| Variable                      | Default         | Purpose                                  |
| ----------------------------- | --------------- | ---------------------------------------- |
| `VAULT_BACKUP_DIR`            | `/data/backups` | Where local `tar.gz` archives are written. |
| `VAULT_BACKUP_RETENTION_DAYS` | `30`            | How long to keep backups (0 = forever).  |
| `VAULT_TRASH_RETENTION_DAYS`  | `30`            | How long soft-deleted models stay in trash before permanent deletion. |

Backups can be mirrored to object storage with the `VAULT_BACKUP_S3_*` variables
(`BUCKET`, `ENDPOINT_URL`, `REGION`, `ACCESS_KEY`, `SECRET_KEY`). This is
**independent** of your vault storage backend — a common setup is keeping vault
data on local disk while shipping nightly backups off to R2. See
[Backup & restore](/PrintStash/guides/backup-and-restore/).

## Frontend

| Variable             | Default               | Purpose                                  |
| -------------------- | --------------------- | ---------------------------------------- |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000` | WebSocket base the browser uses for live printer status. Must be reachable from the user's browser, not just the container. |

:::note
**Settings are frozen at startup.** Environment values are read once when the
process boots and never mutated afterward. A small set of values can be
overridden at runtime through the admin UI overlay, but the environment is the
source of truth on boot. This is a deliberate design choice — see
[ADR-0002 in Architecture](/PrintStash/reference/architecture/#adr-0002--frozen-settings--runtime-overlay).
:::

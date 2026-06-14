---
title: Configuration
description: Environment variables for storage, auth, uploads, and backups.
---

PrintStash is configured through environment variables, typically via the `.env`
file copied from `.env.example`. Backend settings use the `VAULT_` prefix.

:::caution
Always change `VAULT_JWT_SECRET` before exposing PrintStash beyond localhost.
Treat it as a credential — anyone with it can mint valid tokens.
:::

## Core

| Variable                            | Default                                  | Purpose                                       |
| ----------------------------------- | ---------------------------------------- | --------------------------------------------- |
| `VAULT_JWT_SECRET`                  | `changeme_jwt_secret_please_change`      | Signing secret for auth tokens. **Change it.** |
| `VAULT_JWT_ALGORITHM`               | `HS256`                                  | JWT signing algorithm.                         |
| `VAULT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60`                                     | Access-token lifetime in minutes.             |
| `VAULT_CORS_ORIGINS`                | _(empty)_                                | Comma-separated allowed origins for the API.  |
| `VAULT_MAX_UPLOAD_MB`               | `512`                                    | Maximum upload size per file, in MB.          |
| `VAULT_LOG_LEVEL`                   | `INFO`                                   | Backend log verbosity.                        |

## Storage

| Variable                  | Default                                   | Purpose                                      |
| ------------------------- | ----------------------------------------- | -------------------------------------------- |
| `VAULT_STORAGE_BACKEND`   | `local`                                   | `local` disk or `s3` object storage.         |
| `VAULT_DATA_DIR`          | `/data/files`                             | Model/file storage root (local backend).     |
| `VAULT_THUMB_DIR`         | `/data/thumbs`                            | Generated thumbnails.                        |
| `VAULT_STAGING_DIR`       | `/data/staging`                           | Incoming/staging area for uploads.           |
| `VAULT_DB_URL`            | `sqlite:////data/db/printstash.sqlite`    | SQLite by default; set a Postgres URL to use Postgres. |

### S3 / R2-compatible object storage

Set `VAULT_STORAGE_BACKEND=s3` and provide credentials. Works with AWS S3 and
S3-compatible services such as Cloudflare R2 or MinIO.

| Variable                                | Default      | Purpose                                  |
| --------------------------------------- | ------------ | ---------------------------------------- |
| `VAULT_S3_BUCKET`                       | _(empty)_    | Bucket name.                             |
| `VAULT_S3_ENDPOINT_URL`                 | _(empty)_    | Custom endpoint (R2/MinIO).              |
| `VAULT_S3_REGION`                       | `auto`       | Region.                                  |
| `VAULT_S3_ACCESS_KEY`                   | _(empty)_    | Access key.                              |
| `VAULT_S3_SECRET_KEY`                   | _(empty)_    | Secret key.                              |
| `VAULT_S3_PRESIGNED_URL_EXPIRE_SECONDS` | `900`        | Lifetime of presigned download URLs.     |
| `VAULT_S3_MULTIPART_THRESHOLD_MB`       | `50`         | Multipart-upload threshold.              |
| `VAULT_S3_LIFECYCLE_EXPIRATION_DAYS`    | `0`          | Object expiration (0 = disabled).        |
| `VAULT_S3_LIFECYCLE_TRANSITION_DAYS`    | `0`          | Days before storage-class transition.    |
| `VAULT_S3_TRANSITION_STORAGE_CLASS`     | `STANDARD_IA`| Storage class to transition into.        |

## Backups & retention

| Variable                      | Default        | Purpose                                  |
| ----------------------------- | -------------- | ---------------------------------------- |
| `VAULT_BACKUP_DIR`            | `/data/backups`| Local backup destination.                |
| `VAULT_BACKUP_RETENTION_DAYS` | `30`           | How long to keep backups.                |
| `VAULT_TRASH_RETENTION_DAYS`  | `30`           | How long soft-deleted models stay in trash. |

Backups can also be pushed to object storage with the `VAULT_BACKUP_S3_*`
variables (`BUCKET`, `ENDPOINT_URL`, `REGION`, `ACCESS_KEY`, `SECRET_KEY`),
mirroring the S3 settings above.

## Frontend

| Variable             | Default                   | Purpose                          |
| -------------------- | ------------------------- | -------------------------------- |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000`     | WebSocket base for live status.  |

:::note
Settings are frozen from the environment at startup. A small set of values can
be overridden at runtime through the admin UI overlay, but environment values
are the source of truth on boot.
:::

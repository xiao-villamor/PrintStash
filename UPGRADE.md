# PrintStash Upgrade Guide

This guide covers supported self-hosted upgrades. SQLite plus local filesystem
storage remains the default. Always upgrade from a fresh backup and retain the
previous application image until validation is complete.

## 0.13.0 notes

- Before starting 0.13.0, record and mount the exact local data, thumbnail, and
  external-library roots used by the old installation. For S3-compatible
  storage, preserve the existing bucket, endpoint, credentials, and data-root
  namespace. Do not point the upgraded application at an empty replacement.
- PrintStash now binds managed roots to durable installation identities. A
  missing or mismatched root enters read-only recovery: startup does not create
  the absent mount, scan it as empty, mark indexed files missing, or delete
  storage bytes. An administrator must verify the exact mount and explicitly
  enroll or re-enroll an eligible legacy root in Settings before writes resume.
- Existing local backup archives and backup-S3 objects are left in place.
  Validated archives without an ownership record require explicit adoption in
  Settings before restore or deletion. S3 discovery checks both the historical
  `nexus3d-backups/` prefix and the current `printstash-backups/` prefix; new
  archives are written only to `printstash-backups/`.
- When multiple locations contain the same backup id, Settings identifies each
  exact source independently. Review its bucket/namespace, prefix, key, size,
  digest, and provider identity before adoption, restore, or deletion.
- Keep the previous image, database, secrets key, and complete storage snapshot
  for the rollback window. After upgrade, verify a new upload and scan, Artifact
  download, trash/restore/permanent-purge behavior, and backup
  create/verify/download/restore against the configured storage provider.

## 0.12.1 notes

This patch has no database migrations or configuration changes. API images now
isolate legacy or operator-supplied `uv run` commands from the root-owned build
cache so unprivileged startup cannot fail on its permissions.

## 0.12.0 notes

- Existing bcrypt password hashes remain valid. A successful login verifies
  the legacy hash and replaces it with Argon2; no offline password migration is
  required.
- PostgreSQL URLs using `postgres://`, `postgresql://`, or the legacy
  `postgresql+psycopg2://` form are normalized to the Psycopg 3 dialect.
  Custom images or scripts that import `psycopg2` or `asyncpg` directly must be
  updated to `psycopg`.
- `aiosqlite` is no longer installed by default. Local development that
  explicitly creates an async SQLite engine must install `--extra async-db`.
- The default `printstash-api` image remains the full image. The light Compose
  file now pulls `printstash-api-lite`, which omits browser-assisted imports and
  STEP tessellation while retaining normal mesh thumbnails.
- Compose-managed MinIO moved to the transitional migration file. If the
  installation owns a `printstash_minio` volume, follow
  [the MinIO migration guide](./docs/minio-migration.md) before changing storage
  settings. The helper does not delete the source and will be removed in 1.0.
- Pending/running import jobs left by a restart are marked failed/retryable.
  Completed and partial states now reflect outputs verified after commit.
- Database/API changes are additive. The new import-job fields are applied by
  the normal startup migration path.

## Before upgrading

- Record the currently deployed image tag and Compose project name.
- Create and download a fresh PrintStash backup.
- If using SQLite, separately preserve the database volume/file and secrets
  key. If using S3-compatible storage, preserve its credentials and bucket.
- Stop slicer hooks, scheduled imports, and other writers during the upgrade.
- Read the target version's changelog entry and its known limitations.

## Docker Compose

The API image runs database migrations before serving requests. Do not add a
manual Alembic command or override the image entrypoint.

```bash
docker compose pull
docker compose up -d --wait
docker compose ps
curl -fsS http://localhost:3000/api/v1/health
```

For the lite deployment:

```bash
docker compose -f docker-compose.light.yml pull
docker compose -f docker-compose.light.yml up -d --wait
```

If building locally:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.build.yml \
  up -d --build --wait
```

Published release images are:

- `ghcr.io/xiao-villamor/printstash-api:<version>` (browser + STEP)
- `ghcr.io/xiao-villamor/printstash-api-lite:<version>` (compact core)
- `ghcr.io/xiao-villamor/printstash-frontend:<version>`

Pin `PRINTSTASH_VERSION` in `.env` for reproducible deployments.

## Local development

```bash
cd backend
uv sync --extra dev --extra full
uv run alembic upgrade head
```

Add `--extra async-db` only when exercising SQLite through
`create_async_engine`.

## Validation after upgrade

- Sign in and confirm setup/users, models, collections, tags, and statistics.
- Upload a mesh and verify its authenticated WebP thumbnail appears without a
  manual reload.
- Upload/import representative G-code and archive inputs; wait for Task Center
  to report a durable terminal state.
- Open authenticated health details and verify the image capabilities match
  the selected full/lite variant.
- For PostgreSQL or S3-compatible deployments, verify database/storage health
  and download representative Artifacts and thumbnails.
- PrintStash no longer creates S3 buckets or changes bucket lifecycle policies.
  Provision the data bucket before startup. Existing
  `VAULT_S3_LIFECYCLE_*` settings are ignored and may be removed; application
  credentials no longer need `s3:CreateBucket` or
  `s3:PutLifecycleConfiguration`. Read access to lifecycle configuration is
  still used to warn about rules that could expire managed objects.
- If migrating MinIO, run Vault Maintenance/audit after switching to SeaweedFS
  and keep the source volume for the rollback window.

## Rollback

Stop the upgraded containers before rollback. Restore the pre-upgrade database,
files/object storage, thumbnails, and secrets key together, then start the
previous image tag. Schema downgrades against live upgraded data are not the
supported rollback path.

For recovery details, see
[Disaster recovery](./docs/disaster-recovery.md).

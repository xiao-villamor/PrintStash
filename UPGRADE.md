# PrintStash Upgrade Guide

This guide covers supported self-hosted upgrades. SQLite plus local filesystem
storage remains the default. Always upgrade from a fresh backup and retain the
previous application image until validation is complete.

## 0.13.0 notes

Start with the [0.13.0 release and migration guide](./docs/0.13.0-release-guide.md)
for a guided path through backup, compatibility boot, validation, new feature
setup, and rollback. The notes below are the detailed storage and database
contract for that process.

- The database migration is additive. It preserves legacy mounted External
  Library rows as `mounted`, adds remote connection/source metadata, durable
  discovery cursors and tombstones, source verification timestamps, and the GC
  plan tables. No migration copies, renames, uploads, or deletes Artifact bytes.
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
- Scheduled retention no longer crosses directly from expiry to hard deletion.
  It creates a bounded GC preview. Automatic physical deletion requires exact
  administrator approval, Verified active storage, a fully verified backup no
  more than 24 hours old on an independent S3 provider, and the quarantine
  configured by `VAULT_GC_QUARANTINE_DAYS` (seven days by default).

### Database and application migration

Before upgrading, run and retain a pre-upgrade database backup plus a storage
snapshot. The supported application startup runs the Alembic chain. Do not edit
the new migrations or generate an alternative schema by hand.

After startup, verify:

1. Every pre-0.13 External Library is shown as a mounted source with the same
   root, collection mode, schedule and linked Artifacts.
2. Legacy linked files have no fabricated remote connection or source key.
   Their source key is populated only by a successful mounted scan.
3. The Storage provider has the same provider identity and namespace as before.
4. `GET /api/v1/admin/gc` returns no active plan on an installation that had no
   pre-existing 0.13 plan.
5. A Trash restore works before reviewing any expired candidates.

Do not downgrade the upgraded database in place. Rollback means stopping the
new image and restoring the database, secrets key and all managed storage from
the same pre-upgrade snapshot.

### Existing storage: safe upgrade path

The storage providers available before 0.13.0 remain supported: local filesystem
and generic S3-compatible storage. Upgrading does not require copying, renaming,
or re-uploading managed objects. Do not switch provider presets during the first
upgrade boot; first prove the existing configuration and bytes in place.

1. Stop all writers and preserve the database, secrets key, and storage bytes as
   one rollback set.
2. Keep the previous storage configuration unchanged:
   - **Local:** retain the exact `VAULT_DATA_DIR` and `VAULT_THUMB_DIR` mounts.
     An empty replacement mount is not the old storage, even when it uses the
     same container path.
   - **S3-compatible:** retain `VAULT_STORAGE_BACKEND=s3` and the existing
     `VAULT_S3_BUCKET`, endpoint, region, access key, and secret key. Leave the
     new typed-provider fields unset for this first boot. The historical
     `vault-data/` object prefix is pinned during the database upgrade; existing
     keys are neither moved nor rewritten.
3. Start 0.13.0 normally. If Settings reports read-only recovery, stop there:
   restore the exact missing mount, bucket, endpoint, or credentials. Do not
   point PrintStash at an empty location to clear the warning. When the original
   local root is present but has no identity marker, use the explicit enrollment
   action in Settings after verifying the displayed path and evidence.
4. Download at least one pre-upgrade Artifact and thumbnail, then upload and
   download one new Artifact. Confirm that both old and new content are present
   before enabling scheduled writers or destructive maintenance.
5. Moving an existing S3 installation to the typed `s3` provider is optional.
   Do it only after the compatibility boot is validated, using the same bucket,
   endpoint, credentials, and root `vault-data`. This changes configuration,
   not object locations; no storage copy should be performed.

The optional provider adoption is equivalent configuration only. The bucket,
endpoint, region, addressing style and `vault-data` root must still resolve to
the same object namespace. PrintStash does not provide a general in-place byte
mover between providers in this release.

### Add a remote Library source

Remote Library sources are new catalog views, not a migration of managed Vault
storage. After the existing installation passes the compatibility boot:

1. Open **Settings > Library sources**.
2. Create and probe an encrypted S3, WebDAV or SFTP connection.
3. Add a source with an optional prefix and run a manual scan.
4. Compare a downloaded linked Artifact's SHA-256 with the source.
5. Keep the source read-only until the full validation checklist passes. Remote
   write-back remains disabled by design.

WebDAV and SFTP require the full image. The lite image cannot activate their
OpenDAL/SSH adapters. See [Library sources](./docs/library-sources.md).

Cloudflare R2, Backblaze B2, Wasabi, self-hosted S3, Nextcloud, WebDAV, and SFTP
presets are new in 0.13.0. An existing generic S3 installation does not need to
select a vendor preset merely because its bucket is hosted by that vendor.

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

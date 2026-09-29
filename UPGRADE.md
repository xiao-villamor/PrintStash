# PrintStash Upgrade Guide

## 0.14.0: background work on a durable engine

Background work now runs as Jobs on an embedded engine (DBOS). Nothing new has
to be installed or configured for the default single-container deployment.

- **Finish or cancel imports first.** Work that is queued or running when you
  upgrade is not carried over: those Jobs are marked failed with a message
  saying so, and the files they had staged are reclaimed by the usual staging
  cleanup. Retry them after the upgrade.
- **Previews and metadata are kept.** Existing thumbnails and metadata are
  recorded as already derived, so the library is not re-rendered. Meshes that
  never had geometry are derived in the background after startup.
- **Engine state is disposable.** On SQLite it is `printstash-dbos.sqlite`
  beside the vault database; on PostgreSQL the `dbos` schema of the same
  database. It is not part of a backup, and a restore rebuilds it. Do not copy
  it between installations.
- **API changes for scripts and integrations.** Poll `GET /api/v1/jobs/{id}`
  instead of `/api/v1/ingest/jobs/{id}`. Job states are `queued`, `running`,
  `interrupted`, `completed`, `failed` and `cancelled`. `POST /api/v1/backups`
  returns `202` with a `job_id`; the finished Job's `result` is the backup.
  `POST /api/v1/files/thumbnails/rebuild` is replaced by
  `POST /api/v1/admin/work/derivatives/thumbnail/regenerate` with
  `{"mode": "all"}`. A binary G-code toolpath that is still being prepared
  answers `202` with `{"state": …}`. Pending Imports report `job_id`.
- **Optional workers.** On PostgreSQL, with one volume every process mounts
  (`VAULT_SHARED_STORAGE=true`), you can add
  `python -m app.worker` processes: `docker-compose.advanced.yml --profile workers`
  runs them; see
  [Background work and workers](docs/deployment.md#background-work-and-workers).
  A worker never migrates; it waits for the API, which migrates on start.
- **`VAULT_INGEST_WORKER_COUNT` is gone.** Nothing read it; uploads committed
  at once are `VAULT_JOBS_INGEST_CONCURRENCY` (default 2), or Settings →
  Background work.

## 0.14.0: Model Family removal

This upgrade removes the Model Families feature and its database tables. Back up
both the database and managed storage before upgrading if you need to retain
Family names, roles, comparison measurements, or uploaded covers. The migration
keeps every Model, Artifact, G-code Revision, print job, Collection, and Multipart
Model; Family relationship metadata is not restored by a downgrade. Existing
portable archives remain importable for their Models and Multipart Models, but
Family entries and views filtered by Family are skipped.
Uploaded Family cover blobs may remain in managed storage without a live
reference after the database tables are removed.

## 0.14.0: canonical Artifact downloads

Clients using `/api/v1/files/{id}/download-url` or `download-direct` must use
`/api/v1/files/{id}/download` and follow temporary redirects. Authentication is
still required. Browser provider delivery needs readable CORS configuration;
without it, downloads continue through the API. See [Artifact downloads](docs/artifact-delivery.md).


This guide covers supported self-hosted upgrades. SQLite plus local filesystem
storage remains the default. Always upgrade from a fresh backup and retain the
previous application image until validation is complete.

## 0.14.0: one data volume

Both Compose files now mount **one** volume, `printstash`, at `/data` instead of
five (`printstash_data`, `printstash_thumbs`, `printstash_db`,
`printstash_staging`, `printstash_backups`). With staging and the library on one
mount, imports publish by hard link instead of copying every file; see
[Data and host folders](docs/deployment.md#data-and-host-folders). PrintStash
does not migrate the old volumes itself: a new `docker-compose.yml` against old
volumes starts **empty, at first-run setup**, while the old volumes stay
untouched. Move the data once, before starting the new file.

**Unraid, CasaOS and other dashboards** that map one folder to `/data`: nothing
to do.

**Host folders** that were already `<folder>/files`, `<folder>/thumbs`,
`<folder>/db`, `<folder>/staging` and `<folder>/backups`: replace the five
mounts with `<folder>:/data`. Nothing is copied.

**Named volumes** (the default files): copy them into the new volume once. This
needs free space equal to your data.

```bash
# In your install directory, with the old docker-compose.yml still in place:
docker compose down             # never `down -v`
docker volume ls --filter name=printstash_

# Replace docker-compose.yml with the new file, then create (not start) the
# stack so Compose owns the new volume:
docker compose up --no-start

P=printstash    # your project name: the prefix `docker volume ls` showed
docker run --rm \
  -v "${P}_printstash_db:/from/db:ro" \
  -v "${P}_printstash_data:/from/files:ro" \
  -v "${P}_printstash_thumbs:/from/thumbs:ro" \
  -v "${P}_printstash_staging:/from/staging:ro" \
  -v "${P}_printstash_backups:/from/backups:ro" \
  -v "${P}_printstash:/to" \
  alpine sh -c 'cp -a /from/. /to/'

docker compose up -d
```

`cp -a` keeps owners, permissions and timestamps, and the storage-root markers
travel with the files, so the library opens as the same installation. Check
that your models, thumbnails and backups are there, then remove the five old
volumes with `docker volume rm`. For `docker-compose.advanced.yml`, add
`-f docker-compose.advanced.yml` to each `docker compose` command.

If you set `VAULT_DATA_DIR`, `VAULT_THUMB_DIR`, `VAULT_STAGING_DIR` or
`VAULT_BACKUP_DIR` yourself, they still work as per-directory overrides. Keep
staging on the same mount as the library, or imports fall back to copying.

## 0.14.0: fewer Compose files

The repository root now has two Compose files. `docker-compose.yml` runs
PrintStash as **one container** (web UI + full API, image
`ghcr.io/xiao-villamor/printstash`); `docker-compose.advanced.yml` is the old
two-container stack with every setting wired. If you downloaded a Compose file
with `curl` into your own directory, nothing changes until you replace it.

If you run PrintStash **from a git checkout**, check which file you used before
pulling:

| You used | Use now |
| --- | --- |
| `docker-compose.simple.yml` | `docker-compose.yml` (one container; plain `docker compose up -d --remove-orphans`) |
| `docker-compose.yml` (plain `docker compose`) | `docker-compose.advanced.yml` — the old default moved here. **Add `-f docker-compose.advanced.yml` to every command**, or the new `docker-compose.yml` starts without your `.env` settings (PostgreSQL, S3, SSO, secrets) |
| `docker-compose.light.yml` | `docker-compose.yml`, or `docker-compose.advanced.yml` with the `printstash-api-lite` image to keep the smaller image or your `.env` values |
| `docker-compose.prod.yml` | `docker-compose.advanced.yml` with a `.env` that sets `VAULT_JWT_SECRET`, `VAULT_SETUP_MODE=disabled` and `VAULT_SESSION_COOKIE_SECURE=true`; bind the port to `127.0.0.1` as its comment shows |
| `docker-compose.build.yml` / `docker-compose.light.build.yml` | Uncomment the `build:` blocks in `docker-compose.advanced.yml` |
| `docker-compose.unified.yml` | `docker-compose.yml` (same content) |
| `docker-compose.migrate-minio.yml` | `deploy/minio-migration/compose.yml` together with `docker-compose.advanced.yml` |

Both files mount the same `printstash` volume, so data is found as long as the
Compose project name (normally the directory name) stays the same; coming from
the five older volumes, first follow [one data volume](#0140-one-data-volume).
Stop the old stack
with `docker compose -f <old file> down` (never `down -v`) before starting the
new one. Moving from two containers to the single container, run
`docker compose up -d --remove-orphans` so the old `frontend` and `api` containers
release port 3000. Custom settings move from `services.api.environment` and
`services.frontend.environment` to `services.printstash.environment`, and logs
come from `docker compose logs printstash`.

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

For the advanced deployment, add `-f docker-compose.advanced.yml` to each
command. If building locally, uncomment its `build:` blocks and run:

```bash
docker compose -f docker-compose.advanced.yml up -d --build --wait
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

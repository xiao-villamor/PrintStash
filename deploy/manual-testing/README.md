# PrintStash pre-release manual-test stack

This is a disposable, isolated Compose project for release validation. It
builds the current checkout and runs PrintStash with PostgreSQL and S3
(SeaweedFS), plus Spoolman 0.26.0. Authentik 2026.5.5 and the printer
emulators are opt-in profiles. No service uses host networking, the Docker
socket, or a Redis dependency.

Do not use this harness for production or real credentials. The checked-in
defaults are deliberately public test values. Published ports bind to
`127.0.0.1`; use a separate copied env file if multiple people share a host.
The canonical env filename in every command below is
`deploy/manual-testing/.env`.

## Prerequisites and environment

Run commands from the repository root. Docker Compose v2, `curl`, `jq`, and a
POSIX shell are required on the host. Copy the test-only values once:

```sh
cp deploy/manual-testing/.env.example deploy/manual-testing/.env
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env config
```

The config command only renders YAML; it does not pull or start images. Keep
the copied `.env` local. `PRINTSTASH_IMAGE_TAG=candidate` makes locally built
API/frontend images addressable as `printstash-manual-api:candidate` and
`printstash-manual-frontend:candidate`.

## Two supported database modes

Both modes use S3 for model/file objects. The default mode is PostgreSQL and
is the realistic deployment topology. The SQLite mode exists specifically to
exercise PrintStash's integrated backup/restore implementation. Integrated
PrintStash PostgreSQL backup/restore is intentionally unsupported; use the
external `pg_dump`/`pg_restore` procedure below for PostgreSQL.

These modes pin database and storage settings into the application runtime
configuration. Never switch PostgreSQL+S3, SQLite+S3, or SQLite+local on
retained configured volumes: changing `VAULT_DB_URL` or the storage backend is
not a migration. Export the paired database/object evidence first, then reset
before changing mode (or use a separately copied checkout/project):

```sh
# First follow the PostgreSQL+S3 or SQLite backup export below.
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env down -v --remove-orphans
```

### Default: PostgreSQL + S3 + Authentik

The copied env file already selects PostgreSQL and OIDC. Start the core stack
with the `identity` profile, then prove the application and OIDC provider are
actually ready (Compose health alone does not prove blueprint reconciliation):

```sh
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env --profile identity \
  up --wait --build
./deploy/manual-testing/bin/wait-ready.sh deploy/manual-testing/.env
```

### SQLite + S3 without identity

Use shell overrides for a clean one-command mode switch; they take precedence
over values in `.env`. Do not include `--profile identity` in this mode:

```sh
VAULT_DB_URL=sqlite:////data/db/printstash.sqlite \
VAULT_OIDC_ENABLED=false \
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env \
  up --wait --build
VAULT_OIDC_ENABLED=false ./deploy/manual-testing/bin/wait-ready.sh \
  deploy/manual-testing/.env
```

The PostgreSQL container may remain present because it is part of this single
Compose project, but the API does not connect to it in SQLite mode. Keep the
same mode for a backup/restore test: changing `VAULT_DB_URL` against retained
data is not a migration.

### SQLite + local storage for external-library write-back

External-library indexing works against S3, but PrintStash's deliberate
write-back guard requires the vault storage backend to be local. Use this
separate disposable mode when testing an upload/revision written back into the
mounted library (it is not the PostgreSQL+S3 or SQLite+S3 backup mode):

```sh
VAULT_DB_URL=sqlite:////data/db/printstash.sqlite \
VAULT_STORAGE_BACKEND=local \
VAULT_OIDC_ENABLED=false \
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env \
  up --wait --build
VAULT_OIDC_ENABLED=false ./deploy/manual-testing/bin/wait-ready.sh \
  deploy/manual-testing/.env
```

For first-run setup in this mode, send the same setup request below with
`storage_backend` set to `local` (the S3 fields may be omitted). Then enable
the external-library feature, create `/manual-external`, ingest a model with
that library as its target, and verify the new file is created below the host
fixture directory. Repeat with the same filename to exercise the collision
safe suffix, then read the host directory back before deleting the fixture. In
the web import dialog, select the external library as the target; that is the
write-back operation covered by this mode.

### Optional printer emulators

Add the `emulators` profile to either mode. They are built from the checked-in
core testkit, so no package installation occurs at container startup:

```sh
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env --profile identity --profile emulators \
  up --wait --build
```

For the SQLite/no-identity mode, omit `--profile identity` and add only
`--profile emulators`.

## URLs and deterministic test credentials

| Service | Host URL | Default credential or key |
| --- | --- | --- |
| PrintStash | <http://localhost:3100> | local admin created during setup |
| PrintStash API | <http://localhost:8100/api/v1/health> | none |
| Authentik | <http://localhost:9000> | `akadmin` / `authentik-admin-test` |
| Authentik OIDC discovery | <http://authentik.localhost:9000/application/o/printstash/.well-known/openid-configuration> | public |
| Spoolman | <http://localhost:7912> | no authentication |
| SeaweedFS S3 | <http://localhost:8333> | `printstash-manual` / `printstash-manual-s3-secret` |
| SeaweedFS master | <http://localhost:9333> | no authentication |
| Moonraker emulator | <http://localhost:7125> | no key by default |
| OctoPrint emulator | <http://localhost:5000> | `X-Api-Key: printstash-manual-octoprint-key` |
| PrusaLink emulator | <http://localhost:8080> | `X-Api-Key: printstash-manual-prusalink-key` |

All OIDC usernames, passwords, emails, client values, and test API keys can
be overridden in `.env`. Authentik's blueprint creates:

- `printstash-admin` / `printstash-admin-test`, in `printstash-admins`;
- `printstash-user` / `printstash-user-test`, in `printstash-users`.

The API receives `http://authentik.localhost:9000` through a Compose network
alias, while browsers resolve the reserved `.localhost` name to loopback. The
same dual-resolution rule applies to S3 presigned URLs through
`http://seaweedfs.localhost:8333`. Host/container ports **9000 and 8333 are
fixed** because changing either would invalidate the signed issuer or
presigned endpoint. Other published ports are overridable in `.env` and remain
loopback-only. If a local resolver does not honor `.localhost`, add
`127.0.0.1 authentik.localhost seaweedfs.localhost` to the host resolver.

`VAULT_OIDC_ALLOW_INSECURE_HTTP=true` is present only for this local harness.
Never copy it into a production deployment.

## First-run setup and readiness

After `up --wait`, complete the initial local admin setup. The following is an
exact S3-backed setup request; it also saves an admin bearer token for the
remaining API examples:

```sh
requested_storage_backend="${VAULT_STORAGE_BACKEND-}"
set -a; . deploy/manual-testing/.env; set +a
if test -n "$requested_storage_backend"; then VAULT_STORAGE_BACKEND="$requested_storage_backend"; fi
API=http://localhost:${PRINTSTASH_API_PORT:-8100}
SETUP_JSON="$(jq -n \
  --arg token "$VAULT_SETUP_TOKEN" \
  --arg s3 "$VAULT_S3_BUCKET" \
  --arg endpoint "$VAULT_S3_ENDPOINT_URL" \
  --arg region "$VAULT_S3_REGION" \
  --arg access "$SEAWEEDFS_ACCESS_KEY" \
  --arg secret "$SEAWEEDFS_SECRET_KEY" \
  --arg backend "${VAULT_STORAGE_BACKEND:-s3}" \
  '{setup_token:$token,username:"manual-admin",password:"manual-admin-password",email:"manual-admin@example.test",storage_backend:$backend,s3_bucket:$s3,s3_endpoint_url:$endpoint,s3_region:$region,s3_access_key:$access,s3_secret_key:$secret}')"
ADMIN_TOKEN="$(curl -fsS -X POST "$API/api/v1/setup" \
  -H 'Content-Type: application/json' --data "$SETUP_JSON" | jq -r .access_token)"
test -n "$ADMIN_TOKEN" && test "$ADMIN_TOKEN" != null
curl -fsS "$API/api/v1/health" | jq
```

For the SQLite + local write-back mode, run `export VAULT_STORAGE_BACKEND=local`
before this setup block; the preservation line above keeps that override after
loading `.env`. The default block leaves the copied `s3` value unchanged.

For a later shell, obtain the same token with local login:

```sh
ADMIN_TOKEN="$(curl -fsS -X POST "$API/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  --data '{"username":"manual-admin","password":"manual-admin-password"}' \
  | jq -r .access_token)"
```

With `VAULT_OIDC_ENABLED=true`, run `wait-ready.sh` only with the `identity`
profile and then test both blueprint users in separate browser profiles. The
script proves PrintStash health, Authentik discovery, a non-empty JWKS, and
PrintStash's own `/auth/providers` status. With `VAULT_OIDC_ENABLED=false`,
start without `identity`; the script proves health and the disabled provider
state instead.

## Spoolman seed and integration

Seed deterministic, idempotent data and read it back from the pinned Spoolman
0.26 API:

```sh
set -a; . deploy/manual-testing/.env; set +a
SPOOLMAN_BASE_URL=http://localhost:${SPOOLMAN_PORT:-7912} \
  ./deploy/manual-testing/bin/seed-spoolman.sh
curl -fsS http://localhost:7912/api/v1/vendor | jq
curl -fsS http://localhost:7912/api/v1/filament | jq
curl -fsS http://localhost:7912/api/v1/spool | jq
```

In PrintStash, configure Spoolman with API base URL
`http://spoolman:8000` (not `localhost`) and run filament sync. Verify the
seeded spool is selectable, a print decrements it once, and a repeated status
poll does not decrement it again. The script matches by vendor/name/location,
so rerunning it does not create duplicates.

## External library fixture

The host path in `MANUAL_EXTERNAL_LIBRARY_PATH` is mounted read-write at
`/manual-external`, outside PrintStash's private `/data` tree. It is disposable
test data, not a backup or a source of truth for private storage. Enable the
feature with the local admin token, create a fixture, create the library, and
read it back:

```sh
curl -fsS -X PUT "$API/api/v1/config" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' --data '{"external_libraries_enabled":true}' | jq .external_libraries_enabled
mkdir -p deploy/manual-testing/external-library/fixture-a
printf '; PrintStash manual fixture\nG1 X1 Y1 E1\n' \
  > deploy/manual-testing/external-library/fixture-a/manual.gcode
chmod -R u+rwX,go+rX deploy/manual-testing/external-library
LIBRARY_JSON='{"name":"Manual external fixture","root_path":"/manual-external","enabled":true,"scan_schedule":"","watch_mode":"off","collection_mode":"mirror"}'
LIBRARY="$(curl -fsS -X POST "$API/api/v1/libraries" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data "$LIBRARY_JSON")"
LIBRARY_ID="$(jq -r .id <<<"$LIBRARY")"
JOB_ID="$(curl -fsS -X POST "$API/api/v1/libraries/$LIBRARY_ID/scan" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r .job_id)"
for attempt in $(seq 1 30); do
  JOB_JSON="$(curl -fsS "$API/api/v1/ingest/jobs/$JOB_ID" -H "Authorization: Bearer $ADMIN_TOKEN")"
  JOB_STATE="$(jq -r .state <<<"$JOB_JSON")"
  case "$JOB_STATE" in completed|failed|cancelled) break;; esac
  sleep 1
done
test "$JOB_STATE" = completed
printf '%s\n' "$JOB_JSON" | jq
curl -fsS "$API/api/v1/libraries" -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

For a collision check, copy identical bytes under a second name and trigger a
second scan. Read `last_scan_summary` plus the resulting model/file list and
confirm the duplicate policy is deterministic and source files remain intact:

```sh
cp deploy/manual-testing/external-library/fixture-a/manual.gcode \
  deploy/manual-testing/external-library/fixture-a/manual-copy.gcode
COLLISION_JOB_ID="$(curl -fsS -X POST "$API/api/v1/libraries/$LIBRARY_ID/scan" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r .job_id)"
for attempt in $(seq 1 30); do
  COLLISION_JOB_JSON="$(curl -fsS "$API/api/v1/ingest/jobs/$COLLISION_JOB_ID" \
    -H "Authorization: Bearer $ADMIN_TOKEN")"
  COLLISION_STATE="$(jq -r .state <<<"$COLLISION_JOB_JSON")"
  case "$COLLISION_STATE" in completed|failed|cancelled) break;; esac
  sleep 1
done
test "$COLLISION_STATE" = completed
printf '%s\n' "$COLLISION_JOB_JSON" | jq
curl -fsS "$API/api/v1/libraries/$LIBRARY_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq .last_scan_summary
curl -fsS "$API/api/v1/models?limit=100" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

Delete the index and prove the source was not deleted, then remove only the
fixture directory during cleanup:

```sh
curl -fsS -X DELETE "$API/api/v1/libraries/$LIBRARY_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
test -f deploy/manual-testing/external-library/fixture-a/manual.gcode
rm -rf deploy/manual-testing/external-library/fixture-a
```

If a host-created fixture is unreadable in the container, repair only this
scoped directory with `chmod -R u+rwX,go+rX`; do not broaden permissions on the
repository or Docker volumes.

## Release, backup, upgrade, and rollback

Run the repository manual checklist against <http://localhost:3100>, including
uploads, previews, S3 downloads, trash/restore, search, collections, tags,
documents, auth, Spoolman, and each emulator. Inspect logs before sign-off:

```sh
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env logs --tail=200 api frontend seaweedfs spoolman
```

### SQLite integrated backup/restore

In SQLite mode, create and verify a PrintStash backup from the UI or API. With
`ADMIN_TOKEN` set:

```sh
curl -fsS -X POST "$API/api/v1/backups" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | tee /tmp/printstash-backup.json | jq
BACKUP_ID="$(jq -r .backup_id /tmp/printstash-backup.json)"
curl -fsS -X POST "$API/api/v1/backups/$BACKUP_ID/verify" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
mkdir -p deploy/manual-testing/evidence/sqlite-backup
curl -fsS "$API/api/v1/backups/$BACKUP_ID/download" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -o "deploy/manual-testing/evidence/sqlite-backup/$BACKUP_ID.tar.gz"
test -s "deploy/manual-testing/evidence/sqlite-backup/$BACKUP_ID.tar.gz"
sha256sum "deploy/manual-testing/evidence/sqlite-backup/$BACKUP_ID.tar.gz"
curl -fsS "$API/api/v1/backups/capabilities/database" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

The capability response must report SQLite create/restore support. Keep the
backup archive outside Docker volumes before any reset. To exercise restore,
change a known marker in the UI, then restore the saved archive and verify the
marker reverted (the endpoint is destructive and requires the admin token):

```sh
curl -fsS -X POST "$API/api/v1/backups/$BACKUP_ID/restore" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | tee /tmp/printstash-restore.json | jq
curl -fsS "$API/api/v1/backups/$BACKUP_ID/verify" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

### PostgreSQL + S3 external snapshot

Before upgrading or resetting, export the database and SeaweedFS volume to the
ignored evidence directory. The top-level Compose name makes the volume name
deterministic:

```sh
set -a; . deploy/manual-testing/.env; set +a
mkdir -p deploy/manual-testing/evidence/pre-upgrade
docker volume inspect printstash-manual_printstash_manual_seaweedfs
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env exec -T printstash-db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  > deploy/manual-testing/evidence/pre-upgrade/printstash.dump
docker run --rm \
  -v printstash-manual_printstash_manual_seaweedfs:/from:ro \
  -v "$PWD/deploy/manual-testing/evidence/pre-upgrade:/to" alpine:3.20 \
  sh -c 'tar czf /to/seaweedfs.tgz -C /from .'
sha256sum deploy/manual-testing/evidence/pre-upgrade/printstash.dump \
  deploy/manual-testing/evidence/pre-upgrade/seaweedfs.tgz
```

The dump and object snapshot are one rollback point; restore both together.
Stop API/frontend first, restore the object volume, recreate the PostgreSQL
database, and restore the dump:

```sh
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env stop api frontend
docker run --rm \
  -v printstash-manual_printstash_manual_seaweedfs:/to \
  -v "$PWD/deploy/manual-testing/evidence/pre-upgrade:/from:ro" alpine:3.20 \
  sh -c 'find /to -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar xzf /from/seaweedfs.tgz -C /to'
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env exec -T printstash-db \
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$POSTGRES_DB' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";" \
  -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env exec -T printstash-db \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner \
  < deploy/manual-testing/evidence/pre-upgrade/printstash.dump
```

### Exact previous release → candidate upgrade

The harness keeps source builds and release images under the same local image
names. This example tests v0.12.2 as the previous release and the current
checkout as `candidate`, while retaining volumes:

```sh
PREVIOUS_VERSION=0.12.2
docker pull ghcr.io/xiao-villamor/printstash-api:$PREVIOUS_VERSION
docker pull ghcr.io/xiao-villamor/printstash-frontend:$PREVIOUS_VERSION
docker tag ghcr.io/xiao-villamor/printstash-api:$PREVIOUS_VERSION printstash-manual-api:previous
docker tag ghcr.io/xiao-villamor/printstash-frontend:$PREVIOUS_VERSION printstash-manual-frontend:previous
PRINTSTASH_IMAGE_TAG=previous docker compose -p printstash-manual \
  -f docker-compose.manual-test.yml --env-file deploy/manual-testing/.env \
  --profile identity up --wait --no-build
./deploy/manual-testing/bin/wait-ready.sh deploy/manual-testing/.env
# Seed a marker and exercise the old release. Capture this exact paired
# PostgreSQL+S3 checkpoint before switching image tags.
mkdir -p deploy/manual-testing/evidence/pre-upgrade
set -a; . deploy/manual-testing/.env; set +a
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env exec -T printstash-db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  > deploy/manual-testing/evidence/pre-upgrade/printstash.dump
docker run --rm -v printstash-manual_printstash_manual_seaweedfs:/from:ro \
  -v "$PWD/deploy/manual-testing/evidence/pre-upgrade:/to" alpine:3.20 \
  sh -c 'tar czf /to/seaweedfs.tgz -C /from .'
PRINTSTASH_IMAGE_TAG=candidate docker compose -p printstash-manual \
  -f docker-compose.manual-test.yml --env-file deploy/manual-testing/.env \
  --profile identity up --wait --build
./deploy/manual-testing/bin/wait-ready.sh deploy/manual-testing/.env
```

The final `up --build` uses `PRINTSTASH_IMAGE_TAG=candidate`, builds the
current source into the candidate image names, and retains every volume. Re-run
the checklist and verify the marker, database rows, and S3 objects survived.
For a PostgreSQL rollback, stop API/frontend and restore the paired dump/object
snapshot above, then start again with
`PRINTSTASH_IMAGE_TAG=previous ... up --wait --no-build`. For SQLite rollback,
use the paired SQLite PrintStash backup and object snapshot; do not mix a
PostgreSQL dump with SQLite data.

### Reset and teardown

`down` retains volumes. Before the destructive reset, copy any needed database
dump, S3 archive, and PrintStash backup outside `deploy/manual-testing` (for
example to an encrypted local release-evidence directory). This bounded copy
is an explicit safety checkpoint:

```sh
EVIDENCE_BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/printstash-manual-evidence.XXXXXX")"
cp -a deploy/manual-testing/evidence/. "$EVIDENCE_BACKUP_DIR/"
echo "Release evidence copied to $EVIDENCE_BACKUP_DIR; verify it before reset."
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env down
```

Only after confirming those exports are outside Docker volumes, reset this
disposable project:

```sh
docker compose -p printstash-manual -f docker-compose.manual-test.yml \
  --env-file deploy/manual-testing/.env down -v --remove-orphans
```

This permanently removes the project volumes (PostgreSQL, Authentik,
Spoolman, SeaweedFS, files, thumbnails, staging, database, and backups). It
does not remove the checked-out source or the ignored evidence/fixture files.

## Limitations

- HTTP and deterministic credentials are intentional local-test concessions;
  there is no TLS, mail delivery, reverse proxy, or production hardening.
- Spoolman has no authentication in this harness and is loopback-only.
- Authentik blueprints reconcile asynchronously; `wait-ready.sh` checks the
  actual discovery/JWKS/provider contract and times out after 180 seconds.
- Emulators model provider contracts and state transitions, not firmware,
  hardware faults, MQTT/Bambu devices, cameras, or real printer TLS.
- S3 snapshots are raw SeaweedFS volume archives. Keep the database dump and
  object archive from the same point in time; restoring only one can leave
  dangling rows or objects.

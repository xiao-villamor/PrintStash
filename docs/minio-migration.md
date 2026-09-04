# Migrating bundled MinIO data to SeaweedFS

PrintStash no longer starts MinIO from its normal Compose stack. External MinIO
endpoints remain supported through the generic S3 settings. This guide only
applies to installations that used the old Compose-managed
`printstash_minio` volume.

The migration helper is transitional and will be removed in 1.0. It copies
objects; it never deletes or mutates source objects and never removes the
source volume.

## 1. Preserve the deployment identity

Run the helper from the same checkout and with the same Compose project name as
the existing installation. If the old deployment used `docker compose -p`, set
that project name first so Compose resolves the original volume:

```bash
export COMPOSE_PROJECT_NAME=your-existing-project-name
```

Back up the MinIO volume before migration. Do not rename or remove it.

## 2. Configure source and target

The defaults match the previously bundled services:

```bash
export MINIO_ROOT_USER=minioadmin
export MINIO_ROOT_PASSWORD=minioadmin
export MINIO_MIGRATION_SOURCE_BUCKET=printstash-vault
export SEAWEEDFS_ACCESS_KEY=printstash
export SEAWEEDFS_SECRET_KEY=printstash-secret
export SEAWEEDFS_MIGRATION_TARGET_BUCKET=printstash-vault
```

Use the credentials and bucket name from the existing deployment when they
differ. The temporary MinIO API and console stay on the internal Compose network
and are not published to host ports. Use `docker compose exec` if you need to
inspect the migration containers directly.

## 3. Copy and verify

```bash
./scripts/migrate_minio_to_seaweedfs.sh
```

The command starts the legacy MinIO volume and SeaweedFS, copies with the
digest-pinned rclone image, then runs `rclone check --download --one-way`.
Download comparison verifies object content even when multipart ETags differ.
Extra objects already in the destination are left untouched.

The helper is idempotent: rerunning it copies only changed or missing data and
performs the full verification again. A failed verification exits non-zero.

## 4. Switch PrintStash

After a successful check, configure the API for SeaweedFS:

```bash
VAULT_STORAGE_BACKEND=s3
VAULT_S3_BUCKET=printstash-vault
VAULT_S3_ENDPOINT_URL=http://seaweedfs:8333
VAULT_S3_REGION=us-east-1
VAULT_S3_ACCESS_KEY=printstash
VAULT_S3_SECRET_KEY=printstash-secret
```

Start PrintStash with the `s3` profile, inspect representative models and
thumbnails, and run Vault Maintenance/audit before considering the migration
accepted.

## 5. Retain the source

Stop the compatibility service when validation is complete:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.migrate-minio.yml \
  stop minio
```

Do not run `down --volumes` and do not delete `printstash_minio`. Keep the
source volume through the rollback window defined for your installation.

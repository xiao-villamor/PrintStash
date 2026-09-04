#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if docker compose -f "$repo_root/docker-compose.yml" config --services | grep -qx minio; then
  echo "MinIO must not be present in the normal Compose stack" >&2
  exit 1
fi

test_id="${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-1}"
export COMPOSE_PROJECT_NAME="printstash-minio-test-${test_id}"
export MINIO_MIGRATION_CONTAINER_NAME="${COMPOSE_PROJECT_NAME}-minio"
export SEAWEEDFS_CONTAINER_NAME="${COMPOSE_PROJECT_NAME}-seaweedfs"
export MINIO_MIGRATION_SOURCE_BUCKET=migration-source
export SEAWEEDFS_MIGRATION_TARGET_BUCKET=migration-target

compose=(
  docker compose
  --project-directory "$repo_root"
  -f "$repo_root/docker-compose.yml"
  -f "$repo_root/docker-compose.migrate-minio.yml"
  --profile s3
  --profile minio-migration
)

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" up -d --wait minio seaweedfs

"${compose[@]}" run --rm --no-deps --entrypoint /bin/sh minio-migrate -c '
  set -eu
  mkdir -p /tmp/seed
  printf "ordinary object\n" >/tmp/seed/ordinary.txt
  printf "unicode object\n" >/tmp/seed/modelo-ñ-雪.stl
  dd if=/dev/urandom of=/tmp/seed/multipart.bin bs=1M count=12 2>/dev/null
  rclone mkdir "minio:${MINIO_MIGRATION_SOURCE_BUCKET}"
  rclone copyto /tmp/seed/ordinary.txt "minio:${MINIO_MIGRATION_SOURCE_BUCKET}/ordinary.txt"
  rclone copyto /tmp/seed/modelo-ñ-雪.stl "minio:${MINIO_MIGRATION_SOURCE_BUCKET}/unicode/modelo-ñ-雪.stl"
  rclone copyto /tmp/seed/multipart.bin "minio:${MINIO_MIGRATION_SOURCE_BUCKET}/multipart.bin" \
    --s3-upload-cutoff 5Mi --s3-chunk-size 5Mi
'

"$repo_root/scripts/migrate_minio_to_seaweedfs.sh"
"$repo_root/scripts/migrate_minio_to_seaweedfs.sh"

"${compose[@]}" run --rm --no-deps --entrypoint /bin/sh minio-migrate -c '
  set -eu
  test "$(rclone lsf "minio:${MINIO_MIGRATION_SOURCE_BUCKET}" --recursive --files-only | wc -l)" -eq 3
  test "$(rclone lsf "seaweedfs:${SEAWEEDFS_MIGRATION_TARGET_BUCKET}" --recursive --files-only | wc -l)" -eq 3
  rclone check "minio:${MINIO_MIGRATION_SOURCE_BUCKET}" \
    "seaweedfs:${SEAWEEDFS_MIGRATION_TARGET_BUCKET}" --download --one-way
'

docker volume inspect "${COMPOSE_PROJECT_NAME}_printstash_minio" >/dev/null
echo "MinIO migration integration test passed twice with normal, Unicode, and multipart objects."

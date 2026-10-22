#!/bin/sh
# Container entrypoint: bring the database to the latest schema, then exec the
# image command (CMD: uvicorn).
#
# Running migrations here — inside the image, on every start — means they happen
# however the container is launched (Compose, Portainer, Unraid, bare `docker
# run`), so a missing/edited `command:` can no longer skip them (issue #29).
# `app.db.migrate` is idempotent (a no-op at head) and self-heals an un-stamped
# "orphan" database. `set -e` aborts startup if a migration fails — before the
# app serves a single request, which is exactly when you want to find out.
set -e

# WSL2 hands its GPU to a container as /dev/dxg. Mesa reaches it through D3D12
# with the host's own libraries, which the operator mounts at /usr/lib/wsl, so
# name them here rather than asking everyone to know the variables. Anything the
# operator already set wins. With no device nothing is exported: the image is the
# same on every host, and the GPU is a runtime choice (docs/compute.md).
if [ -e "${PRINTSTASH_WSL_GPU_DEVICE:-/dev/dxg}" ]; then
  : "${GALLIUM_DRIVER:=d3d12}"
  : "${MESA_LOADER_DRIVER_OVERRIDE:=d3d12}"
  : "${XDG_RUNTIME_DIR:=/tmp}"
  # The entrypoint runs again after dropping privileges: add the path once.
  case ":${LD_LIBRARY_PATH:-}:" in
    *:/usr/lib/wsl/lib:*) ;;
    *) LD_LIBRARY_PATH="/usr/lib/wsl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
  esac
  export GALLIUM_DRIVER MESA_LOADER_DRIVER_OVERRIDE XDG_RUNTIME_DIR LD_LIBRARY_PATH
fi

if [ "${PUID+x}" != x ]; then PUID=10001; fi
if [ "${PGID+x}" != x ]; then PGID=10001; fi

die_invalid_id() {
  echo "PrintStash: $1 must be a positive numeric Linux user/group ID (got '$2')" >&2
  exit 64
}

validate_id() {
  name=$1
  value=$2
  case "$value" in
    ''|*[!0-9]*) die_invalid_id "$name" "$value" ;;
  esac

  # Keep the comparison in the shell, but suppress the implementation's
  # integer-overflow diagnostic for an unreasonably large value. Linux IDs are
  # unsigned 32-bit values; 0 is reserved for root and is deliberately not an
  # accepted runtime identity.
  if ! [ "$value" -le 4294967294 ] 2>/dev/null; then
    die_invalid_id "$name" "$value"
  fi
  if [ "$value" -eq 0 ]; then
    die_invalid_id "$name" "$value"
  fi
}

canonicalize_id() {
  value=$1
  while [ "${value#0}" != "$value" ] && [ -n "${value#0}" ]; do
    value=${value#0}
  done
  printf '%s' "$value"
}

# Validate before checking the current uid so malformed configuration can
# never reach migrations or the server, even when an operator starts the image
# with an explicit non-root Docker user.
validate_id PUID "$PUID"
validate_id PGID "$PGID"
PUID=$(canonicalize_id "$PUID")
PGID=$(canonicalize_id "$PGID")

if [ "$(id -u)" = "0" ]; then
  requested_identity="$PUID:$PGID"

  # One volume at the data root holds every app-owned path, each at the same
  # child the app derives (app/core/config.py DATA_ROOT_LAYOUT). A directory
  # can still be moved with its own variable; an empty one means the default.
  data_root=${VAULT_DATA_ROOT:-/data}
  files_dir=${VAULT_DATA_DIR:-$data_root/files}
  thumb_dir=${VAULT_THUMB_DIR:-$data_root/thumbs}
  staging_dir=${VAULT_STAGING_DIR:-$data_root/staging}
  backup_dir=${VAULT_BACKUP_DIR:-$data_root/backups}
  artifact_cache_dir=${VAULT_ARTIFACT_CACHE_ROOT:-$data_root/artifact-cache}
  embedding_cache_dir=${VAULT_EMBEDDING_CACHE_DIR:-$data_root/ai-models}

  # Named volumes are created by Docker, while bind mounts may not exist yet.
  # Creating the configured roots here keeps the ownership repair below
  # deterministic and preserves the local-first defaults.
  mkdir -p "$data_root/db" "$files_dir" "$thumb_dir" "$staging_dir" "$backup_dir" \
    "$artifact_cache_dir" "$embedding_cache_dir"

  # Numeric ownership works for host-created bind mounts even when the
  # requested uid/gid has no matching /etc/passwd entry in the image. Inspect
  # every startup because a persistent marker is unsafe, but chown only entries
  # that actually differ: even a no-op chown changes ctime and would invalidate
  # PrintStash's inode-bound ownership receipts. `find` does not follow symlinks
  # and `chown -h` changes a mismatched symlink itself, never its target.
  #
  # Only app-owned paths are ours to repair. An external Library source may be
  # mounted anywhere else under the data root, including read-only. `-xdev`
  # still evaluates a mount point and even descends into same-device bind mounts,
  # so walking the whole root would change externally owned data (issue #330).
  find "$data_root" -maxdepth 0 \
    \( ! -uid "$PUID" -o ! -gid "$PGID" \) \
    -exec chown -h "$requested_identity" {} +
  for managed_root in "$data_root/db" "$files_dir" "$thumb_dir" "$staging_dir" \
    "$backup_dir" "$artifact_cache_dir" "$embedding_cache_dir"; do
    find "$managed_root" -xdev \
      \( ! -uid "$PUID" -o ! -gid "$PGID" \) \
      -exec chown -h "$requested_identity" {} +
  done
  # A dropped numeric identity has no group memberships of its own, so a GPU
  # handed in as /dev/dri would be visible but unopenable: its nodes belong to
  # the host's render/video group. Keep every group that owns a GPU node, and
  # every group the operator granted with --group-add / group_add, so mapping
  # the device is the only GPU step on any host. Root's group (0) is never
  # handed on.
  supplementary_groups=$PGID
  add_group() {
    case ",$supplementary_groups," in
      *",$1,"*) ;;
      *) [ "$1" = 0 ] || supplementary_groups="$supplementary_groups,$1" ;;
    esac
  }
  for granted_group in $(id -G); do add_group "$granted_group"; done
  for gpu_node in "${PRINTSTASH_GPU_DEVICE_DIR:-/dev/dri}"/*; do
    # Skip by-path/ and the unmatched glob of a host without a GPU.
    [ -e "$gpu_node" ] && [ ! -d "$gpu_node" ] || continue
    add_group "$(stat -c %g "$gpu_node")"
  done

  # Re-exec the same entrypoint as the requested numeric identity. This keeps
  # migration and operator-supplied commands in the exact existing order while
  # allowing arbitrary positive host IDs without mutating the image's user DB.
  # HOME follows the identity, as a login would set it ("/" with no entry).
  runtime_home=$(getent passwd "$PUID" | cut -d: -f6)
  HOME=${runtime_home:-/}
  export HOME
  exec setpriv --reuid="$PUID" --regid="$PGID" \
    --groups="$supplementary_groups" "$0" "$@"
fi

if [ "$(id -u)" != "$PUID" ] || [ "$(id -g)" != "$PGID" ]; then
  echo "PrintStash: non-root container user $(id -u):$(id -g) does not match PUID:PGID $PUID:$PGID" >&2
  exit 64
fi

# The API owns the schema. A worker (VAULT_PROCESS_ROLE=worker) never migrates:
# several replicas would race, and one of an older build must not downgrade
# what a newer API applied. `python -m app.worker` waits for the schema instead.
if [ "${VAULT_PROCESS_ROLE:-all}" != "worker" ]; then
  /app/.venv/bin/python -m app.db.migrate
fi

exec "$@"

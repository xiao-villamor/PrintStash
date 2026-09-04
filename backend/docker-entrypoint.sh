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

  # Named volumes are created by Docker, while bind mounts may not exist yet.
  # Creating the configured roots here keeps the ownership repair below
  # deterministic and preserves the local-first defaults.
  mkdir -p \
    "${VAULT_DATA_DIR:-/data/files}" \
    "${VAULT_THUMB_DIR:-/data/thumbs}" \
    "${VAULT_STAGING_DIR:-/data/staging}" \
    "${VAULT_BACKUP_DIR:-/data/backups}" \
    /data/db

  # Numeric ownership works for host-created bind mounts even when the
  # requested uid/gid has no matching /etc/passwd entry in the image. Re-own
  # every startup: the data paths are runtime-writable, so a persistent marker
  # there could be replaced by an unprivileged process (including a symlink)
  # before the next root startup.
  chown -hR "$requested_identity" \
    "${VAULT_DATA_DIR:-/data/files}" \
    "${VAULT_THUMB_DIR:-/data/thumbs}" \
    "${VAULT_STAGING_DIR:-/data/staging}" \
    "${VAULT_BACKUP_DIR:-/data/backups}" \
    /data/db
  # Re-exec the same entrypoint as the requested numeric identity. This keeps
  # migration and operator-supplied commands in the exact existing order while
  # allowing arbitrary positive host IDs without mutating the image's user DB.
  exec gosu "$requested_identity" "$0" "$@"
fi

if [ "$(id -u)" != "$PUID" ] || [ "$(id -g)" != "$PGID" ]; then
  echo "PrintStash: non-root container user $(id -u):$(id -g) does not match PUID:PGID $PUID:$PGID" >&2
  exit 64
fi

/app/.venv/bin/python -m app.db.migrate

exec "$@"

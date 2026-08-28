#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
DATA_ROOT="$SCRIPT_DIR/../.storage-data"
API_PORT="${PLAYWRIGHT_STORAGE_API_PORT:-8420}"
WEBDAV_PORT="${PLAYWRIGHT_STORAGE_WEBDAV_PORT:-8775}"
RESTART_TRIGGER="$DATA_ROOT/restart"

rm -rf "$DATA_ROOT"
mkdir -p \
  "$DATA_ROOT/files" \
  "$DATA_ROOT/thumbs" \
  "$DATA_ROOT/staging" \
  "$DATA_ROOT/backups" \
  "$DATA_ROOT/webdav"

export VAULT_DB_URL="sqlite:///$DATA_ROOT/test.sqlite"
export VAULT_DATA_DIR="$DATA_ROOT/files"
export VAULT_THUMB_DIR="$DATA_ROOT/thumbs"
export VAULT_STAGING_DIR="$DATA_ROOT/staging"
export VAULT_BACKUP_DIR="$DATA_ROOT/backups"
export VAULT_JWT_SECRET="e2e-storage-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="e2e-storage-provider-secrets"
export VAULT_STORAGE_ALLOW_UNVERIFIED="true"

cd "$BACKEND_DIR"
PY=(.venv/bin/python)
ALEMBIC=(.venv/bin/alembic)
WSGIDAV=(.venv/bin/wsgidav)

"${ALEMBIC[@]}" upgrade head
"${WSGIDAV[@]}" \
  --host=127.0.0.1 \
  --port="$WEBDAV_PORT" \
  --root="$DATA_ROOT/webdav" \
  --auth=anonymous \
  --no-config \
  --quiet &
WEBDAV_PID=$!

BACKEND_PID=""
start_backend() {
  "${PY[@]}" -m uvicorn app.main:app --port "$API_PORT" --host 127.0.0.1 &
  BACKEND_PID=$!
}

cleanup() {
  if [[ -n "$BACKEND_PID" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  kill "$WEBDAV_PID" 2>/dev/null || true
  wait "$WEBDAV_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start_backend
while kill -0 "$BACKEND_PID" 2>/dev/null; do
  if [[ -f "$RESTART_TRIGGER" ]]; then
    rm -f "$RESTART_TRIGGER"
    kill "$BACKEND_PID"
    wait "$BACKEND_PID" || true
    start_backend
  fi
  sleep 0.2
done

wait "$BACKEND_PID"

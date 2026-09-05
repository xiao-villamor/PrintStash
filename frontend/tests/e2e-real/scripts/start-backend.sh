#!/usr/bin/env bash
# Launch the REAL FastAPI backend against a throwaway SQLite DB + temp data dirs,
# so the Playwright "real" suite drives the actual API, services, and persistence
# instead of a mock. State is wiped on every launch — each run starts empty and
# the auth helper seeds the first admin through /setup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
DATA_ROOT="${PLAYWRIGHT_REAL_DATA_DIR:-$SCRIPT_DIR/../.data}"
PORT="${PLAYWRIGHT_REAL_API_PORT:-8410}"

rm -rf "$DATA_ROOT"
mkdir -p "$DATA_ROOT/files" "$DATA_ROOT/thumbs" "$DATA_ROOT/staging" "$DATA_ROOT/backups"

export VAULT_DB_URL="sqlite:///$DATA_ROOT/test.sqlite"
export VAULT_DATA_DIR="$DATA_ROOT/files"
export VAULT_THUMB_DIR="$DATA_ROOT/thumbs"
export VAULT_STAGING_DIR="$DATA_ROOT/staging"
export VAULT_BACKUP_DIR="$DATA_ROOT/backups"
export VAULT_JWT_SECRET="e2e-real-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="e2e-real-secrets-key"
export VAULT_RESTART_ENABLED="true"

cd "$BACKEND_DIR"
if [ -x .venv/bin/python ]; then
  PY=(.venv/bin/python)
  ALEMBIC=(.venv/bin/alembic)
else
  PY=(uv run python)
  ALEMBIC=(uv run alembic)
fi

if [ -z "${VAULT_BGCODE_EXECUTABLE:-}" ]; then
  VAULT_BGCODE_EXECUTABLE="$("${PY[@]}" -m tests.bgcode_support "$DATA_ROOT/converter")"
  export VAULT_BGCODE_EXECUTABLE
fi

"${ALEMBIC[@]}" upgrade head

# Mirror the official container's restart policy so the real-browser suite can
# exercise the Settings restart flow without weakening production behaviour.
# A graceful uvicorn shutdown exits zero and is relaunched; crashes stay red.
child_pid=""
stop_backend() {
  if [ -n "$child_pid" ]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap stop_backend TERM INT

while true; do
  "${PY[@]}" -m uvicorn app.main:app --port "$PORT" --host 127.0.0.1 &
  child_pid=$!
  if wait "$child_pid"; then
    exit_code=0
  else
    exit_code=$?
  fi
  child_pid=""
  if [ "$exit_code" -eq 0 ] || [ "$exit_code" -eq 143 ]; then
    # Uvicorn may report a handled SIGTERM as either a clean exit or 128+TERM.
    # Both are restart requests here; every other status is a real crash.
    continue
  else
    exit "$exit_code"
  fi
done

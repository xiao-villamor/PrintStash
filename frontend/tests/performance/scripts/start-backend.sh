#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
# Never accept an arbitrary data directory: this launcher owns only this fixture.
DATA_ROOT="$SCRIPT_DIR/../.startup-data"
rm -rf "$DATA_ROOT"
mkdir -p "$DATA_ROOT"
unset VAULT_DB_URL VAULT_DATA_DIR VAULT_THUMB_DIR VAULT_STAGING_DIR VAULT_BACKUP_DIR
export VAULT_DATA_ROOT="$DATA_ROOT"
export VAULT_JWT_SECRET="startup-test-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="startup-test-secrets-key"
cd "$BACKEND_DIR"
if [ -x .venv/bin/python ]; then PY=(.venv/bin/python); else PY=(uv run python); fi
"${PY[@]}" -m app.db.migrate
"${PY[@]}" -m tests.factories.library_startup --distribution "${STARTUP_DISTRIBUTION:-distributed}"
exec "${PY[@]}" -m uvicorn app.main:app --port "${STARTUP_API_PORT:-8420}" --host 127.0.0.1 --log-level warning

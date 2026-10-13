#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CORPUS="${1:?choose distributed or rich}"
case "$CORPUS" in
  distributed) DEFAULT_PORT=8520 ;;
  rich) DEFAULT_PORT=8521 ;;
  *) echo "choose distributed or rich" >&2; exit 1 ;;
esac
# This launcher never reads or resets a caller-supplied data root.
export VAULT_DATA_ROOT="$FRONTEND_DIR/.startup-results/library-acceptance/$CORPUS"
unset VAULT_DB_URL VAULT_DATA_DIR VAULT_THUMB_DIR VAULT_STAGING_DIR VAULT_BACKUP_DIR
export VAULT_JWT_SECRET="performance-fixture-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="performance-fixture-secrets-key"
mkdir -p "$VAULT_DATA_ROOT"
cd "$FRONTEND_DIR/../backend"
if [ -x .venv/bin/python ]; then PY=(.venv/bin/python); else PY=(uv run python); fi
"${PY[@]}" -m app.db.migrate
if [ ! -f "$VAULT_DATA_ROOT/corpus.json" ]; then
  "${PY[@]}" -m tests.factories.library_startup --distribution "$CORPUS"
fi
exec "${PY[@]}" -m uvicorn app.main:app --port "${PERF_API_PORT:-$DEFAULT_PORT}" --host 127.0.0.1 --log-level warning

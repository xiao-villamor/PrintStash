#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
MODEL_ROOT="$(mktemp -d -t printstash-search-ui-XXXXXX)"
child_pid=""
stop_backend() {
  if [ -n "$child_pid" ]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  rm -rf "$MODEL_ROOT"
}
trap stop_backend EXIT
trap 'exit 0' TERM INT

cd "$BACKEND_DIR"
uv run python -m tests.fakes.search_ui_assets "$MODEL_ROOT"
if [ -n "${PLAYWRIGHT_AI_SEARCH_MODEL_DIR:-}" ]; then
  export VAULT_EMBEDDING_LOCAL_MODEL_DIR="$PLAYWRIGHT_AI_SEARCH_MODEL_DIR"
else
  export VAULT_EMBEDDING_LOCAL_MODEL_DIR=""
fi
export VAULT_EMBEDDING_CACHE_DIR="$MODEL_ROOT/cache"
export VAULT_EMBEDDING_DOWNLOAD_ENABLED=false
export VAULT_SEARCH_NATIVE_VECTORS_ENABLED=true
bash "$SCRIPT_DIR/start-backend.sh" &
child_pid=$!
wait "$child_pid"

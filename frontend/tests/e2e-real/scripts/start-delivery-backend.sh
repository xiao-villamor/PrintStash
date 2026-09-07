#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
DATA_ROOT="${PLAYWRIGHT_DELIVERY_DATA_DIR:-$SCRIPT_DIR/../.delivery-data}"
rm -rf "$DATA_ROOT"
mkdir -p "$DATA_ROOT/files" "$DATA_ROOT/thumbs" "$DATA_ROOT/staging" "$DATA_ROOT/backups"
export PLAYWRIGHT_DELIVERY_DATA_DIR="$DATA_ROOT"
export VAULT_DB_URL="sqlite:///$DATA_ROOT/test.sqlite"
export VAULT_DATA_DIR="$DATA_ROOT/files"
export VAULT_THUMB_DIR="$DATA_ROOT/thumbs"
export VAULT_STAGING_DIR="$DATA_ROOT/staging"
export VAULT_BACKUP_DIR="$DATA_ROOT/backups"
export VAULT_JWT_SECRET="e2e-native-delivery-secret-at-least-32-bytes"
export VAULT_SECRETS_KEY="e2e-native-delivery-secrets"
cd "$BACKEND_DIR"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m tests.fakes.browser_delivery
else
  exec uv run python -m tests.fakes.browser_delivery
fi

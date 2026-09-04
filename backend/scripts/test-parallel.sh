#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible alias for the complete parallel lane.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$script_dir/test.sh" full "$@"

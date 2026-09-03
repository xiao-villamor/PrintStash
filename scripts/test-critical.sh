#!/usr/bin/env bash
# Release-blocking workflows: real database/storage/API contracts, then real browsers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

(
  cd "$REPO_ROOT/backend"
  uv run pytest \
    tests/repo/test_critical_capabilities.py::TestCriticalCapabilityManifest::test_every_required_capability_names_a_marked_test \
    -q -p no:randomly
  ./scripts/test.sh critical "$@"
)

(
  cd "$REPO_ROOT/frontend"
  pnpm test:e2e:critical
)

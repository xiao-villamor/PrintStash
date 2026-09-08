#!/bin/bash
# Run both container suites against the same image before CI promotes its digest.
set -euo pipefail
image=${1:?usage: test-unified-image.sh IMAGE}
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
PRINTSTASH_TEST_IMAGE="$image" python3 -m unittest discover -s "$repo_root/backend/unified/tests" -v
bash "$repo_root/backend/unified/tests/test-runtime.sh" "$image"

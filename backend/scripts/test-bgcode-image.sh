#!/bin/sh
# Run on each native full/lite architecture after loading the final image.
set -eu
repository="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
docker run --rm \
  -v "$repository/backend/tests/fixtures/bgcode:/fixtures:ro" \
  --entrypoint /bin/sh "$1" -ec '
    cp /fixtures/prusaslicer.bgcode /tmp/reference.bgcode
    bgcode /tmp/reference.bgcode
    cmp /tmp/reference.gcode /fixtures/prusaslicer.gcode
    /app/.venv/bin/python -c "from pathlib import Path; import printstash_mesh_native as native; path=Path(\"/tmp/reference.bgcode\"); assert native.is_valid_bgcode(path); metadata=native.parse_gcode_metadata(path); assert metadata[\"slicer_name\"] == \"PrusaSlicer\"; assert native.gcode_thumbnails(path)"
  '

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
  '

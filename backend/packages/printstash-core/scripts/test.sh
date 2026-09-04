#!/usr/bin/env bash
#
# Test lanes for the autonomous `printstash_core` package. It installs and tests
# independently of the backend — that independence is the point of the package, so
# it has its own runner rather than a backend lane that would hide a missing
# dependency behind the backend's environment.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: ./scripts/test.sh [lane] [pytest arguments...]

Lanes
  all        the whole suite. No services, no sockets, no database.  (default)
  coverage   `all` under branch coverage, then the coverage gate
             (tests/repo/test_coverage_floors.py). Writes term-missing,
             .coverage-html/index.html and coverage.json.

Anything after the lane goes to pytest:
  ./scripts/test.sh all tests/gcode/test_parser.py -k malformed
  ./scripts/test.sh coverage      # then: open .coverage-html/index.html
EOF
}

lane="${1:-all}"
if (( $# > 0 )); then
  shift
fi

case "$lane" in
  -h|--help|help)
    usage
    exit 0
    ;;
  all)
    exec uv run --project . pytest -m "not coverage_gate" "$@"
    ;;
  coverage)
    # Two passes: the gate reads the report, and the report is only written once
    # the run it measures has finished. See backend/scripts/test.sh for the long
    # version of why a single pass would silently judge the previous run.
    uv run --project . pytest -m "not coverage_gate" \
      --cov --cov-report=term-missing --cov-report=json --cov-report=html "$@"
    echo
    echo "HTML report: $(pwd)/.coverage-html/index.html"
    echo
    exec uv run --project . pytest tests/repo/test_coverage_floors.py -q --no-cov
    ;;
  *)
    echo "unknown lane: $lane" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac

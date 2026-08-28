#!/usr/bin/env bash
#
# Test lanes. A lane is a directory (the tier) plus, at most, a marker that gates a
# subset needing a resource — never a filename heuristic. `./scripts/test.sh --help`
# prints the table below.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: ./scripts/test.sh [lane] [pytest arguments...]

Lanes
  fast       tests/unit + tests/integration, minus `slow`.        (default)
             The feature loop: real SQLite, real routers, no sockets.
  contract   tests/contract — our clients against contract-enforcing fakes
             over a real loopback socket. Needs no external services;
             `s3`-marked cases skip themselves without an endpoint.
  e2e        tests/e2e — the whole app over ASGITransport plus the fakes.
  full       everything, including `slow`, minus the coverage gate.
  coverage   `full` under branch coverage, then the coverage gate: aggregate
             ratchet plus a per-module floor (tests/repo/test_coverage_floors.py).
             Writes term-missing, .coverage-html/index.html and coverage.json.
             This is the lane CI gates on.
  affected   `--testmon`: only tests whose executed lines changed. Seed it with
             one full run first, and never use it as the only pre-merge gate.
  serial     `full` without xdist. For debugging an ordering or state bug.

Anything after the lane goes to pytest, so a path or `-k` still works:
  ./scripts/test.sh fast tests/unit/services/test_gcode_parser.py
  ./scripts/test.sh full -k "trash and not slow" -x

Coverage is measured with branches on, so a guard clause whose false path never
runs counts as half-covered rather than covered. Read the gap three ways:

  ./scripts/test.sh coverage              # the gate, plus term-missing
  open .coverage-html/index.html          # per-line, per-branch, clickable
  ./scripts/test.sh coverage tests/integration/services/test_trash.py
                                          # one module's own contribution

The `postgres`- and `s3`-marked subsets run against a real PostgreSQL and a real
SeaweedFS, started as containers for the run (see backend/tests/containers.py).
`full` therefore needs Docker running and stops with a message if it is not —
skipping them would report a green run that verified none of them. `fast` needs
nothing.
EOF
}

lane="${1:-fast}"
if (( $# > 0 )); then
  shift
fi

case "$lane" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

pytest_args=("$@")
has_target=false
for arg in "${pytest_args[@]:-}"; do
  if [[ "$arg" == *::* || -e "$arg" ]]; then
    has_target=true
    break
  fi
done

parallel=(-n auto --dist worksteal)

# `${a[@]+"${a[@]}"}` rather than `"${a[@]}"` everywhere below. bash 3.2 — still
# the default shell on macOS — treats `"${empty[@]}"` under `set -u` as an unbound
# variable and aborts, so every lane died on its own `exec` line whenever the
# caller passed no extra pytest arguments, which is exactly how
# `./scripts/test.sh coverage` is documented to be run. CI is bash 5, where the
# same expansion is legal, which is why it stayed hidden. The `+` form expands to
# nothing at all when the array is empty, on both.

# Prepend the lane's paths only when the caller did not name a target of their own.
lane_paths=()
add_paths() {
  if [[ "$has_target" == false ]]; then
    lane_paths=("$@")
  fi
}

case "$lane" in
  fast)
    add_paths tests/unit tests/integration
    exec uv run pytest "${parallel[@]}" -m "not slow and not coverage_gate" ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    ;;
  contract)
    add_paths tests/contract
    exec uv run pytest "${parallel[@]}" -m "not coverage_gate" ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    ;;
  e2e)
    add_paths tests/e2e
    exec uv run pytest "${parallel[@]}" -m "not coverage_gate" ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    ;;
  full)
    add_paths tests
    exec uv run pytest "${parallel[@]}" -m "not coverage_gate" ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    ;;
  coverage)
    # Two invocations on purpose. The gate reads `coverage.json`, and that file is
    # only written when the run it measures has finished — so a gate collected into
    # the measured run could only ever read the *previous* run's numbers, which is
    # how a coverage check passes on a report that predates the change it was meant
    # to judge. Hence `not coverage_gate` on the measured pass, and a second, tiny
    # pass that runs nothing else.
    add_paths tests
    uv run pytest "${parallel[@]}" -m "not coverage_gate" \
      --cov --cov-report=term-missing --cov-report=json --cov-report=html \
      ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    echo
    echo "HTML report: $(pwd)/.coverage-html/index.html"
    echo
    exec uv run pytest tests/repo/test_coverage_floors.py -q --no-cov -p no:randomly
    ;;
  affected)
    add_paths tests
    exec uv run pytest "${parallel[@]}" -m "not coverage_gate" --testmon ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    ;;
  serial)
    add_paths tests
    exec uv run pytest -m "not coverage_gate" ${lane_paths[@]+"${lane_paths[@]}"} ${pytest_args[@]+"${pytest_args[@]}"}
    ;;
  *)
    echo "unknown lane: $lane" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac

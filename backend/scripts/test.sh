#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 {fast|contract|e2e|full|affected|serial} [pytest arguments...]"
}

lane="${1:-fast}"
if [[ "$lane" == "--help" || "$lane" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# > 0 )); then
  shift
fi

pytest_args=("$@")
has_target=false
for arg in "${pytest_args[@]}"; do
  if [[ "$arg" == *::* || -e "$arg" ]]; then
    has_target=true
    break
  fi
done
parallel=(-n auto --dist worksteal)

case "$lane" in
  fast)
    if [[ "$has_target" == false ]]; then
      pytest_args=(
        tests/unit
        tests/integration
        "${pytest_args[@]}"
      )
    fi
    exec uv run pytest "${parallel[@]}" -m "not slow" "${pytest_args[@]}"
    ;;
  contract)
    if [[ "$has_target" == false ]]; then
      pytest_args=(tests/contract "${pytest_args[@]}")
    fi
    exec uv run pytest "${parallel[@]}" "${pytest_args[@]}"
    ;;
  e2e)
    if [[ "$has_target" == false ]]; then
      pytest_args=(tests/e2e "${pytest_args[@]}")
    fi
    exec uv run pytest "${parallel[@]}" "${pytest_args[@]}"
    ;;
  affected)
    if [[ "$has_target" == false ]]; then
      pytest_args=(tests packages/printstash-core/tests "${pytest_args[@]}")
    fi
    exec uv run pytest "${parallel[@]}" --testmon "${pytest_args[@]}"
    ;;
  full)
    if [[ "$has_target" == false ]]; then
      pytest_args=(tests packages/printstash-core/tests "${pytest_args[@]}")
    fi
    exec uv run pytest "${parallel[@]}" "${pytest_args[@]}"
    ;;
  serial)
    if [[ "$has_target" == false ]]; then
      pytest_args=(tests packages/printstash-core/tests "${pytest_args[@]}")
    fi
    exec uv run pytest "${pytest_args[@]}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

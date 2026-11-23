#!/usr/bin/env bash
# Opt-in black-box tests against the private, separately checked out Farm Lab.
set -euo pipefail
root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
tooling=${FARM_TEST_TOOLING:-"$(dirname "$root")/printstash-tooling"}
pin=58fad10265638e2801e403a74cf43eb06e53c689
if [[ $(git -C "$tooling" rev-parse HEAD) != "$pin" || -n $(git -C "$tooling" status --porcelain) ]]; then
  echo "Farm Lab requires a clean printstash-tooling checkout at $pin" >&2
  exit 2
fi
tooling=$(cd "$tooling" && pwd)
profiles=(klipper bambu octoprint prusalink elegoo-centauri prusa-mini prusa-mk4s
          prusa-core-one elegoo-neptune4 elegoo-neptune4-plus elegoo-centauri-2)
single=0
if (( $# )); then
  if [[ $# != 2 || $1 != --printer || " ${profiles[*]} " != *" $2 "* || $2 == *" "* || -z $2 ]]; then
    echo "Usage: $0 [--printer PROFILE]; profiles: ${profiles[*]}" >&2
    exit 2
  fi
  single=1
  profiles=("$2")
fi
docker=(docker)
if ! docker info >/dev/null 2>&1; then docker=(sudo -n docker); fi
export FARM_TEST_PRINTSTASH_IMAGE=printstash:emulator-test
cd "$root"
printf 'PrintStash %s, Farm Lab %s\n' "$(git rev-parse HEAD)" "$pin"
if [[ -n $(git status --porcelain) ]]; then echo "Building with local checkout changes."; fi
"${docker[@]}" buildx bake -f docker-bake.hcl unified --set "unified.tags=$FARM_TEST_PRINTSTASH_IMAGE" --load
status=0
"$tooling/farm-lab/tests/printstash_http.sh" "${profiles[@]}" || status=$?
if [[ $status == 130 || $status == 143 ]]; then exit "$status"; fi
observer() {
  PYTHONPATH="$tooling/farm-lab/tests" "${FARM_TEST_PYTHON:-python3}" -B \
    "$root/deploy/manual-testing/bin/farm_observer.py" "$@"
}
observer profiles "${profiles[@]}" || status=$?
if [[ $status == 130 || $status == 143 ]]; then exit "$status"; fi
if (( ! single )); then observer mixed klipper bambu || status=$?; fi
exit "$status"

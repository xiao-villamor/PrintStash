#!/usr/bin/env bash
# Preserve a real released image's testdata backup locally, only after recovery succeeds.
set -euo pipefail
version=${1:?usage: create-demo-backup.sh VERSION FIXTURE_ROOT}
root=${2:?local fixture root required}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Use X.Y.Z' >&2; exit 1; }
repo_root=$(cd "$(dirname "$0")/.." && pwd)
python=${PRINTSTASH_BACKUP_PYTHON:-"$repo_root/backend/.venv/bin/python"}
mkdir -p "$root"
root=$(cd "$root" && pwd)
exec 9>"$root/.create-$version.lock"
flock -n 9 || { echo 'This release fixture is already being created.' >&2; exit 1; }
test ! -e "$root/$version" || { echo 'Historical fixture already exists; refusing replacement.' >&2; exit 1; }
# The corpus comes from the release tag too, not a newer checkout's testdata.
git -C "$repo_root" rev-parse --verify "v$version^{commit}" >/dev/null
work=$(mktemp -d "$root/.building-$version.XXXXXX")
mkdir "$work/corpus" "$work/fixture"
git -C "$repo_root" archive "v$version" testdata | tar -x -C "$work/corpus"
image="ghcr.io/xiao-villamor/printstash:$version"
docker pull "$image"
image=$(docker image inspect "$image" --format '{{.Id}}')
"$python" "$repo_root/scripts/backup_compatibility.py" create --image "$image" \
  --version "$version" --testdata "$work/corpus/testdata" --output "$work/fixture"
"$python" "$repo_root/scripts/backup_compatibility.py" verify --image "$image" \
  --version "$version" --expected-version "$version" --fixture "$work/fixture"
# Failed runs stay under .building-* for diagnosis; only proven fixtures get this name.
mv -T "$work/fixture" "$root/$version"
printf 'Verified historical fixture: %s\n' "$root/$version"

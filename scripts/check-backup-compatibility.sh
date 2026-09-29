#!/usr/bin/env bash
# Restore all locally preserved release backups into disposable candidate containers.
set -euo pipefail
image=${1:?usage: check-backup-compatibility.sh IMAGE EXPECTED_VERSION FIXTURE_ROOT}
candidate=${2:?expected candidate version required}
root=${3:?local fixture root required}
repo_root=$(cd "$(dirname "$0")/.." && pwd)
python=${PRINTSTASH_BACKUP_PYTHON:-"$repo_root/backend/.venv/bin/python"}
# Fail on a missing release fixture instead of quietly narrowing the matrix.
versions=$("$python" - "$candidate" "$repo_root" "$root" <<'PY'
import pathlib, re, subprocess, sys
if not re.fullmatch(r'\d+\.\d+\.\d+', sys.argv[1]):
    raise SystemExit('Use candidate X.Y.Z')
candidate = tuple(map(int, sys.argv[1].split('.')))
tags = subprocess.check_output(['git', '-C', sys.argv[2], 'tag', '--list', 'v*'], text=True).splitlines()
versions = {tuple(map(int, tag[1:].split('.'))) for tag in tags if re.fullmatch(r'v\d+\.\d+\.\d+', tag)}
selected = sorted(v for v in versions if (0, 14, 0) <= v < candidate)
# The first baseline also supports a self-restore on the current release.
if candidate == (0, 14, 0):
    selected = [(0, 14, 0)]
if not selected or selected[0] != (0, 14, 0):
    raise SystemExit('Missing mandatory v0.14.0 historical baseline')
for version in selected:
    text = '.'.join(map(str, version))
    for suffix in ['tar.gz', 'json']:
        path = pathlib.Path(sys.argv[3]) / text / f'demo-backup-{text}.{suffix}'
        if not path.is_file():
            raise SystemExit(f'Missing historical fixture: {path}')
    print(text)
PY
)
# Bind all historical cases to one image even if the caller retags it mid-run.
image=$(docker image inspect "$image" --format '{{.Id}}')
while IFS= read -r version; do
  "$python" "$repo_root/scripts/backup_compatibility.py" verify \
    --image "$image" --expected-version "$candidate" --version "$version" --fixture "$root/$version"
done <<< "$versions"

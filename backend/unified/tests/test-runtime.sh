#!/bin/bash
# Exercise the real final image on each native CI architecture before tagging.
set -euo pipefail
image=${1:?usage: test-unified-image.sh IMAGE}
name="printstash-unified-test-$$"
volume="${name}-data"
cleanup() {
  code=$?
  if [ "$code" -ne 0 ]; then docker logs "$name" >&2 || true; fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker volume create "$volume" >/dev/null
docker run -d --name "$name" -e PUID=12345 -e PGID=23456 \
  -e VAULT_SETUP_MODE=trusted_network \
  -v "$volume:/data" "$image" >/dev/null
ready() {
  for attempt in $(seq 1 90); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' "$name")" = healthy ]; then return; fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != true ]; then break; fi
    sleep 2
  done
  echo 'Unified image did not become healthy' >&2
  return 1
}
ready
# Browser requests reach the SPA and the API through the same listener.
docker exec "$name" sh -ec '
  curl -fsS http://127.0.0.1:3000/library > /tmp/page
  grep -q "<html" /tmp/page
  curl -fsS http://127.0.0.1:3000/api/v1/health > /tmp/health
  test "$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3000/assets/missing.js)" = 404
'
# Identity, actual migrations, SQLite default and optional adapters in final image.
docker exec --user 12345:23456 "$name" /app/.venv/bin/python -c '
import os, sqlite3
from pathlib import Path
import asyncssh, opendal
p = Path("/data/db/printstash.sqlite")
assert p.stat().st_uid == 12345
with sqlite3.connect(p) as db:
    assert db.execute("select version_num from alembic_version").fetchone()
for folder in ("files", "thumbs", "staging", "backups", "db"):
    Path("/data", folder, "unified-smoke").write_text("persisted")
for service, options in (
    ("s3", dict(bucket="build-check", region="us-east-1", access_key_id="build-check", secret_access_key="build-check", disable_config_load="true", disable_ec2_metadata="true")),
    ("webdav", dict(endpoint="https://example.invalid", root="/")),
    ("gdrive", dict(root="/", client_id="build-check", client_secret="build-check", refresh_token="build-check")),
):
    opendal.Operator(service, **options)
'
docker stop --time 60 "$name" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$name")" = 0
docker start "$name" >/dev/null
ready
docker exec --user 12345:23456 "$name" /app/.venv/bin/python -c '
from pathlib import Path
for folder in ("files", "thumbs", "staging", "backups", "db"):
    assert Path("/data", folder, "unified-smoke").read_text() == "persisted"
'
# A real API exit must stop the web process too (Settings restart uses this path).
docker exec "$name" /app/.venv/bin/python -c '
import os, signal
from pathlib import Path
for proc in Path("/proc").iterdir():
    if proc.name.isdigit():
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        if b"app.main:app" in args:
            os.kill(int(proc.name), signal.SIGTERM)
'
exit_code=$(timeout 70 docker wait "$name")
# Uvicorn versions may re-raise SIGTERM after graceful shutdown (128 + 15).
# Both forms must end the container so the orchestrator can restart it.
case "$exit_code" in
  0|143) ;;
  *) echo "Unexpected API restart exit status: $exit_code" >&2; exit 1 ;;
esac
echo 'Unified image smoke test passed'

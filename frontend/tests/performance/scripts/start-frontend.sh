#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export STARTUP_FRONTEND_DIR="${STARTUP_FRONTEND_DIR:-$FRONTEND_DIR}"
export STARTUP_NGINX_DIR="$FRONTEND_DIR/.startup-results/nginx"
mkdir -p "$STARTUP_NGINX_DIR"
python3 - <<'CONFIG'
import os
from pathlib import Path
frontend = Path(os.environ["STARTUP_FRONTEND_DIR"]).resolve()
workspace_frontend = Path(os.environ["STARTUP_NGINX_DIR"]).parents[1]
root = workspace_frontend.parent
output = Path(os.environ["STARTUP_NGINX_DIR"])
server = (workspace_frontend / "nginx.conf").read_text()
server = server.replace("listen 3000;", f"listen {os.environ.get('STARTUP_PORT', '3420')};")
server = server.replace("${NGINX_CLIENT_MAX_BODY_SIZE}", "528m")
server = server.replace("http://api:8000", f"http://127.0.0.1:{os.environ.get('STARTUP_API_PORT', '8420')}")
(output / "server.conf").write_text(server)
(output / "nginx.conf").write_text((root / "backend/unified/nginx.conf").read_text())
CONFIG
STARTUP_CONTAINER="printstash-startup-${STARTUP_PORT:-3420}"
cleanup() { docker rm -f "$STARTUP_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
docker run --rm --network host --name "$STARTUP_CONTAINER" --label printstash.startup=true \
  --mount "type=bind,source=$STARTUP_FRONTEND_DIR/dist,target=/usr/share/nginx/html,readonly" \
  --mount "type=bind,source=$STARTUP_NGINX_DIR/nginx.conf,target=/tmp/startup-nginx.conf,readonly" \
  --mount "type=bind,source=$STARTUP_NGINX_DIR/server.conf,target=/tmp/printstash-nginx-server.conf,readonly" \
  --mount "type=bind,source=$FRONTEND_DIR/security-headers.conf,target=/etc/nginx/security-headers.conf,readonly" \
  nginxinc/nginx-unprivileged:alpine nginx -c /tmp/startup-nginx.conf -g 'daemon off;' &
STARTUP_CONTAINER_PID=$!
wait "$STARTUP_CONTAINER_PID"

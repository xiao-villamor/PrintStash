#!/bin/bash
# Migrations have completed as PUID:PGID before this supervisor starts.
set -eu
# Substitute only the documented setting, preserving nginx's own $variables.
export NGINX_CLIENT_MAX_BODY_SIZE=${NGINX_CLIENT_MAX_BODY_SIZE:-528m}
envsubst '${NGINX_CLIENT_MAX_BODY_SIZE}' < /app/nginx-server.template > /tmp/printstash-nginx-server.conf
nginx -t -c /app/nginx.conf

api_pid=
nginx_pid=
cleanup() {
  trap - EXIT TERM INT
  if [ -n "$api_pid" ]; then kill -TERM "$api_pid" 2>/dev/null || true; fi
  if [ -n "$nginx_pid" ]; then kill -TERM "$nginx_pid" 2>/dev/null || true; fi
  wait || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT

/app/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --proxy-headers &
api_pid=$!
nginx -c /app/nginx.conf -g 'daemon off;' &
nginx_pid=$!

# A Settings restart or either process failing must stop the entire container.
# Docker/Compose then restarts it, including migrations and both services.
status=0
wait -n "$api_pid" "$nginx_pid" || status=$?
exit "$status"

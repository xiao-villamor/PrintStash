#!/usr/bin/env bash
set -euo pipefail

# Proves application readiness, OIDC discovery/JWKS, and PrintStash's own
# provider status. This is deliberately bounded: it never waits forever on a
# failed blueprint or an unhealthy API.
ENV_FILE="${1:-deploy/manual-testing/.env}"
saved_api_port="${PRINTSTASH_API_PORT-}"
saved_oidc_enabled="${VAULT_OIDC_ENABLED-}"
saved_issuer_url="${PRINTSTASH_OIDC_ISSUER_URL-}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
[[ -n "$saved_api_port" ]] && PRINTSTASH_API_PORT="$saved_api_port"
[[ -n "$saved_oidc_enabled" ]] && VAULT_OIDC_ENABLED="$saved_oidc_enabled"
[[ -n "$saved_issuer_url" ]] && PRINTSTASH_OIDC_ISSUER_URL="$saved_issuer_url"

timeout_seconds="${READY_TIMEOUT_SECONDS:-180}"
api_url="http://localhost:${PRINTSTASH_API_PORT:-8100}"
oidc_enabled="${VAULT_OIDC_ENABLED:-true}"
issuer_url="${PRINTSTASH_OIDC_ISSUER_URL:-http://authentik.localhost:9000/application/o/printstash}"
deadline=$((SECONDS + timeout_seconds))
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

get_json() {
  local url="$1" output="$2" status
  status="$(curl -sS -o "$output" -w '%{http_code}' "$url" || true)"
  [[ "$status" == "200" ]]
}

while (( SECONDS < deadline )); do
  if ! get_json "$api_url/api/v1/health" "$tmp_dir/health"; then
    sleep 2
    continue
  fi

  if [[ "$oidc_enabled" != "true" ]]; then
    if get_json "$api_url/api/v1/auth/providers" "$tmp_dir/providers" \
      && jq -e '.oidc_enabled == false' "$tmp_dir/providers" >/dev/null; then
      echo "PrintStash is healthy with OIDC disabled."
      exit 0
    fi
    sleep 2
    continue
  fi

  if ! get_json "$issuer_url/.well-known/openid-configuration" "$tmp_dir/discovery"; then
    sleep 2
    continue
  fi
  if ! jq -e --arg issuer "$issuer_url" '.issuer == $issuer and (.jwks_uri | type == "string")' \
    "$tmp_dir/discovery" >/dev/null; then
    sleep 2
    continue
  fi
  jwks_url="$(jq -r '.jwks_uri' "$tmp_dir/discovery")"
  if ! get_json "$jwks_url" "$tmp_dir/jwks"; then
    sleep 2
    continue
  fi
  if ! jq -e '.keys | type == "array" and length > 0' "$tmp_dir/jwks" >/dev/null; then
    sleep 2
    continue
  fi
  if get_json "$api_url/api/v1/auth/providers" "$tmp_dir/providers" \
    && jq -e '.oidc_enabled == true' "$tmp_dir/providers" >/dev/null; then
    echo "PrintStash, Authentik discovery, JWKS, and OIDC provider are ready."
    exit 0
  fi
  sleep 2
done

echo "Timed out after ${timeout_seconds}s waiting for the manual-test stack." >&2
echo "Check: docker compose --env-file ${ENV_FILE} logs --tail=200 api authentik authentik-worker" >&2
exit 1

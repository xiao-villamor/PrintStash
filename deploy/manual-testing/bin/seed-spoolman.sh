#!/usr/bin/env bash
set -euo pipefail

# Idempotently seeds one vendor, filament, and spool in Spoolman 0.26.x, then
# reads them back. The API shape is intentionally explicit so a release check
# catches a changed Spoolman contract instead of silently creating duplicates.
base_url="${SPOOLMAN_BASE_URL:-http://localhost:${SPOOLMAN_PORT:-7912}}"
vendor_name="${SPOOLMAN_TEST_VENDOR:-PrintStash Manual Vendor}"
filament_name="${SPOOLMAN_TEST_FILAMENT:-PrintStash Manual PLA}"
location="${SPOOLMAN_TEST_LOCATION:-manual-test}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

request() {
  local method="$1" url="$2" body="${3:-}" output="$4" status
  if [[ -n "$body" ]]; then
    status="$(curl -sS -o "$output" -w '%{http_code}' -X "$method" \
      -H 'Content-Type: application/json' --data "$body" "$url" || true)"
  else
    status="$(curl -sS -o "$output" -w '%{http_code}' -X "$method" "$url" || true)"
  fi
  if [[ "$status" != 2* ]]; then
    echo "Spoolman request failed: $method $url (HTTP $status)" >&2
    cat "$output" >&2
    return 1
  fi
}

request GET "$base_url/api/v1/info" '' "$tmp_dir/info"
jq -e '.version | type == "string"' "$tmp_dir/info" >/dev/null

request GET "$base_url/api/v1/vendor" '' "$tmp_dir/vendors"
vendor_id="$(jq -r --arg name "$vendor_name" '.[] | select(.name == $name) | .id' "$tmp_dir/vendors" | head -n1)"
if [[ -z "$vendor_id" ]]; then
  request POST "$base_url/api/v1/vendor" "$(jq -cn --arg name "$vendor_name" '{name:$name}')" "$tmp_dir/vendor"
  vendor_id="$(jq -r '.id' "$tmp_dir/vendor")"
else
  request GET "$base_url/api/v1/vendor/$vendor_id" '' "$tmp_dir/vendor"
fi
jq -e --arg name "$vendor_name" '.name == $name and (.id | type == "number")' "$tmp_dir/vendor" >/dev/null

request GET "$base_url/api/v1/filament" '' "$tmp_dir/filaments"
filament_id="$(jq -r --arg name "$filament_name" --argjson vendor_id "$vendor_id" \
  '.[] | select(.name == $name and .vendor.id == $vendor_id) | .id' "$tmp_dir/filaments" | head -n1)"
if [[ -z "$filament_id" ]]; then
  filament_payload="$(jq -cn --arg name "$filament_name" --argjson vendor_id "$vendor_id" \
    '{vendor_id:$vendor_id,name:$name,material:"PLA",density:1.24,diameter:1.75,weight:1000,color_hex:"3366cc"}')"
  request POST "$base_url/api/v1/filament" "$filament_payload" "$tmp_dir/filament"
  filament_id="$(jq -r '.id' "$tmp_dir/filament")"
else
  request GET "$base_url/api/v1/filament/$filament_id" '' "$tmp_dir/filament"
fi
jq -e --arg name "$filament_name" --argjson vendor_id "$vendor_id" \
  '.name == $name and .vendor.id == $vendor_id and (.id | type == "number")' "$tmp_dir/filament" >/dev/null

request GET "$base_url/api/v1/spool" '' "$tmp_dir/spools"
spool_id="$(jq -r --argjson filament_id "$filament_id" --arg location "$location" \
  '.[] | select(.filament.id == $filament_id and .location == $location and .archived == false) | .id' "$tmp_dir/spools" | head -n1)"
if [[ -z "$spool_id" ]]; then
  spool_payload="$(jq -cn --argjson filament_id "$filament_id" --arg location "$location" \
    '{filament_id:$filament_id,remaining_weight:1000,spool_weight:200,location:$location,archived:false}')"
  request POST "$base_url/api/v1/spool" "$spool_payload" "$tmp_dir/spool"
  spool_id="$(jq -r '.id' "$tmp_dir/spool")"
else
  request GET "$base_url/api/v1/spool/$spool_id" '' "$tmp_dir/spool"
fi
jq -e --argjson filament_id "$filament_id" --arg location "$location" \
  '.filament.id == $filament_id and .location == $location and .archived == false' "$tmp_dir/spool" >/dev/null

echo "Spoolman seed verified: vendor_id=${vendor_id} filament_id=${filament_id} spool_id=${spool_id}"

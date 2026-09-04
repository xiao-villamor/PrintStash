#!/usr/bin/env bash
set -euo pipefail

frontend_base_url="${1:-http://localhost:3000}"
frontend_base_url="${frontend_base_url%/}"
compression_tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "$compression_tmp_dir"' EXIT

index_html="$(
  curl --fail --show-error --silent \
    --retry 5 --retry-delay 1 --retry-all-errors \
    "$frontend_base_url/"
)"

javascript_path="$(
  printf '%s' "$index_html" \
    | grep -oE 'src="[^"]+\.js"' \
    | head -n 1 \
    | cut -d '"' -f 2
)"
stylesheet_path="$(
  printf '%s' "$index_html" \
    | grep -oE 'href="[^"]+\.css"' \
    | head -n 1 \
    | cut -d '"' -f 2
)"

if [[ -z "$javascript_path" || -z "$stylesheet_path" ]]; then
  printf 'Could not find built JavaScript and CSS assets in index.html\n' >&2
  exit 1
fi

assert_gzip_response() {
  local asset_path="$1"
  local header_file="$2"

  curl --fail --show-error --silent \
    --header 'Accept-Encoding: gzip' \
    --dump-header "$header_file" \
    --output /dev/null \
    "$frontend_base_url$asset_path"

  if ! grep -Eqi '^Content-Encoding:[[:space:]]*gzip' "$header_file"; then
    printf '%s was not served with gzip content encoding\n' "$asset_path" >&2
    exit 1
  fi
  if ! grep -Eqi '^Vary:.*Accept-Encoding' "$header_file"; then
    printf '%s did not vary on Accept-Encoding\n' "$asset_path" >&2
    exit 1
  fi
}

assert_gzip_response "$javascript_path" "$compression_tmp_dir/javascript.headers"
assert_gzip_response "$stylesheet_path" "$compression_tmp_dir/stylesheet.headers"

curl --fail --show-error --silent --compressed \
  --output "$compression_tmp_dir/stylesheet.css" \
  "$frontend_base_url$stylesheet_path"
font_path="$(
  grep -oE "/assets/[^)\"']+\\.woff2" "$compression_tmp_dir/stylesheet.css" \
    | head -n 1
)"
if [[ -z "$font_path" ]]; then
  printf 'Could not find a built WOFF2 asset in %s\n' "$stylesheet_path" >&2
  exit 1
fi

curl --fail --show-error --silent \
  --header 'Accept-Encoding: gzip' \
  --dump-header "$compression_tmp_dir/font.headers" \
  --output /dev/null \
  "$frontend_base_url$font_path"
if grep -Eqi '^Content-Encoding:' "$compression_tmp_dir/font.headers"; then
  printf '%s should not be recompressed\n' "$font_path" >&2
  exit 1
fi

printf 'Verified gzip delivery for %s and %s; %s remains uncompressed\n' \
  "$javascript_path" "$stylesheet_path" "$font_path"

#!/usr/bin/env bash
set -euo pipefail

full_image="${1:?full image name required}"
lite_image="${2:?lite image name required}"
minimum_delta=$((700 * 1024 * 1024))

full_size="$(docker image inspect "$full_image" --format '{{.Size}}')"
lite_size="$(docker image inspect "$lite_image" --format '{{.Size}}')"
delta=$((full_size - lite_size))
if (( delta < minimum_delta )); then
  echo "lite image is only $((delta / 1024 / 1024)) MiB smaller than full; expected at least 700 MiB" >&2
  exit 1
fi

docker run --rm --entrypoint /app/.venv/bin/python "$full_image" -c \
  'import importlib.util as i; assert all(i.find_spec(x) for x in ("patchright", "cascadio", "numpy", "PIL", "trimesh"))'
docker run --rm --entrypoint /app/.venv/bin/python "$lite_image" -c \
  'import importlib.util as i; assert not i.find_spec("patchright"); assert not i.find_spec("cascadio"); assert not i.find_spec("aiosqlite"); assert all(i.find_spec(x) for x in ("numpy", "PIL", "trimesh"))'

startup_median_ms() {
  local image="$1"
  local prefix="$2"
  local measurements=()
  local container started elapsed attempt
  for attempt in 1 2 3; do
    container="printstash-${prefix}-startup-${attempt}"
    started="$(date +%s%3N)"
    docker run -d --name "$container" \
      -e VAULT_JWT_SECRET=printstash-image-check-secret-0123456789abcdef \
      "$image" >/dev/null
    for _ in $(seq 1 300); do
      if docker exec "$container" curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
    if ! docker exec "$container" curl -fsS http://localhost:8000/api/v1/health >/dev/null; then
      docker logs "$container" >&2
      docker rm -f "$container" >/dev/null
      return 1
    fi
    elapsed=$(( $(date +%s%3N) - started ))
    measurements+=("$elapsed")
    docker rm -f "$container" >/dev/null
  done
  printf '%s\n' "${measurements[@]}" | sort -n | sed -n '2p'
}

full_startup="$(startup_median_ms "$full_image" full)"
lite_startup="$(startup_median_ms "$lite_image" lite)"
relative_limit=$((full_startup * 110 / 100))
jitter_limit=$((full_startup + 1000))
maximum_lite=$((relative_limit > jitter_limit ? relative_limit : jitter_limit))
if (( lite_startup > maximum_lite )); then
  echo "lite median startup ${lite_startup}ms exceeds the ${maximum_lite}ms limit (full=${full_startup}ms)" >&2
  exit 1
fi

echo "full=$((full_size / 1024 / 1024))MiB lite=$((lite_size / 1024 / 1024))MiB delta=$((delta / 1024 / 1024))MiB"
echo "startup median: full=${full_startup}ms lite=${lite_startup}ms"

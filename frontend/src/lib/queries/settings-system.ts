/** One cache entry per settings system read, including manual release-feed refresh. */
import { queryOptions } from "@tanstack/react-query";
import { getHealthDetails, getLatestRelease } from "@/lib/api/config";
import type { HealthResponse } from "@/types";

export const settingsSystemKeys = {
  health: ["settings-system", "health"] as const,
  release: ["settings-system", "release"] as const,
};
export function settingsHealthOptions() {
  return queryOptions({
    queryKey: settingsSystemKeys.health,
    queryFn: ({ signal }) => getHealthDetails<HealthResponse>({ signal }),
    retry: false,
  });
}
export function settingsReleaseOptions(refresh = false) {
  return queryOptions({
    queryKey: settingsSystemKeys.release,
    queryFn: ({ signal }) => getLatestRelease(refresh, { signal }),
    retry: false,
  });
}

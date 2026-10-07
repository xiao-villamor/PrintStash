import { getJson, sendAction, sendJson } from "@/lib/api/request";

export interface ArtifactCachePolicy {
  enabled: boolean;
  root: string;
  max_bytes: number;
  max_entries: number;
  max_fills: number;
  headroom_bytes: number;
  verify_every_hits: number;
  fill_wait_seconds: number;
}

export interface ArtifactCacheRead {
  policy: ArtifactCachePolicy;
  effective_root: string;
  restart_required: boolean;
  source: string;
  available: boolean;
  health: string;
  labels: { representation: string; backend: string };
  usage: {
    bytes?: number;
    entries?: number;
    leases?: number;
    reserved_bytes?: number;
    fills?: number;
    hits?: number;
    misses?: number;
    hit_ratio_percent?: number;
    bytes_saved?: number;
    completed_fills?: number;
    publication_failures?: number;
    corruptions?: number;
    errors?: number;
    evictions?: number;
    bypasses?: number;
    last_verification?: number;
    pending_eviction_bytes?: number;
    maintenance_running?: number;
  };
}

export const artifactCacheApi = {
  read: () => getJson<ArtifactCacheRead>("/api/v1/config/artifact-cache", {}),
  save: (policy: ArtifactCachePolicy) =>
    sendJson<ArtifactCacheRead>("/api/v1/config/artifact-cache", "PUT", policy),
  reset: async () => {
    await sendAction("/api/v1/config/artifact-cache", "DELETE");
    return getJson<ArtifactCacheRead>("/api/v1/config/artifact-cache", {});
  },
  clear: () => sendJson<ArtifactCacheRead>("/api/v1/config/artifact-cache/clear", "POST", {}),
};

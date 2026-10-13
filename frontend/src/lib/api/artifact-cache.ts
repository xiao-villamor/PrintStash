import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import { requireEditingBase, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";

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

export interface ArtifactCacheRead extends EditingBase {
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

type CacheWriteOptions = GetJsonOptions & { base: EditingBase };
async function writePolicy(
  method: "PUT" | "DELETE",
  policy: ArtifactCachePolicy | null,
  options: CacheWriteOptions,
) {
  const { base } = options;
  requireEditingBase(base);
  const row = await requestApi<ArtifactCacheRead>("/api/v1/config/artifact-cache", {
    method,
    headers: {
      ...jsonHeaders(),
      "If-Match": `"vault-config-e${base.edit_epoch}-v${base.edit_version}"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    },
    body: policy === null ? undefined : JSON.stringify(policy),
    signal: options.signal,
  });
  requireEditingReceipt(row, base);
  return row;
}
export const artifactCacheApi = {
  read: (options?: GetJsonOptions) =>
    getJson<ArtifactCacheRead>("/api/v1/config/artifact-cache", options),
  save: (policy: ArtifactCachePolicy, options: CacheWriteOptions) =>
    writePolicy("PUT", policy, options),
  reset: (options: CacheWriteOptions) => writePolicy("DELETE", null, options),
  clear: (options: GetJsonOptions = {}) =>
    requestApi<ArtifactCacheRead>("/api/v1/config/artifact-cache/clear", {
      method: "POST",
      headers: jsonHeaders(),
      body: "{}",
      signal: options.signal,
    }),
};

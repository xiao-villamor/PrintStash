import { requireEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { getJson, sendJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import { SpoolmanStatus, SpoolmanTestResult, SpoolmanUpdate, SpoolRead } from "@/types";

export function getSpoolmanStatus(options: GetJsonOptions = {}): Promise<SpoolmanStatus> {
  return getJson<SpoolmanStatus>("/api/v1/spoolman", options);
}

export async function updateSpoolman(
  body: SpoolmanUpdate,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<SpoolmanStatus> {
  requireEditingBase(options.base);
  const row = await requestApi<SpoolmanStatus>("/api/v1/spoolman", {
    method: "PUT",
    body: JSON.stringify(body),
    signal: options.signal,
    headers: {
      ...jsonHeaders(),
      "If-Match": `"spoolman-settings-e${options.base.edit_epoch}-v${options.base.edit_version}"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    },
  });
  requireEditingReceipt(row, options.base);
  return row;
}

export function testSpoolman(
  override?: {
    base_url?: string;
    api_key?: string;
  },
  options: GetJsonOptions = {},
): Promise<SpoolmanTestResult> {
  return requestApi<SpoolmanTestResult>("/api/v1/spoolman/test", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(override ?? {}),
    signal: options.signal,
  });
}

export function listSpools(
  includeArchived = false,
  options: GetJsonOptions = {},
): Promise<SpoolRead[]> {
  const q = includeArchived ? "?include_archived=true" : "";
  return getJson<SpoolRead[]>(`/api/v1/spoolman/spools${q}`, options);
}

export interface SpoolmanSyncResult {
  created: number;
  updated: number;
  adopted: number;
  unlinked: number;
}

export function syncSpoolmanFilaments(): Promise<SpoolmanSyncResult> {
  return sendJson<SpoolmanSyncResult>("/api/v1/spoolman/sync-filaments", "POST", {});
}

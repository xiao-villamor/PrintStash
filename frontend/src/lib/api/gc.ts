import { getJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";

export type GcPlanState =
  | "preview"
  | "quarantined"
  | "finalizing"
  | "completed"
  | "aborted"
  | "blocked";

export interface GcPlanItem {
  id: number;
  resource_kind: string;
  resource_id: number;
  key_count: number;
  size_bytes: number;
  deleted_at_snapshot: string;
}

export interface GcPlan {
  id: number;
  state: GcPlanState;
  digest: string;
  resource_count: number;
  candidate_pool_count: number;
  key_count: number;
  size_bytes: number;
  quarantine_until: string | null;
  backup_id: string | null;
  last_error: string | null;
  items: GcPlanItem[];
}

export function getActiveGcPlan(options: GetJsonOptions = {}): Promise<GcPlan | null> {
  return getJson<GcPlan | null>("/api/v1/admin/gc", { ...options, fresh: true });
}

export function createGcPlan(options: Pick<GetJsonOptions, "signal"> = {}): Promise<GcPlan> {
  return requestApi<GcPlan>("/api/v1/admin/gc", {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function approveGcPlan(
  id: number,
  digest: string,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<GcPlan> {
  return requestApi<GcPlan>(`/api/v1/admin/gc/${id}/approve`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ digest }),
    signal: options.signal,
  });
}

export function abortGcPlan(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<GcPlan> {
  return requestApi<GcPlan>(`/api/v1/admin/gc/${id}/abort`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

export function finalizeGcPlan(
  id: number,
  options: Pick<GetJsonOptions, "signal"> = {},
): Promise<GcPlan> {
  return requestApi<GcPlan>(`/api/v1/admin/gc/${id}/finalize`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options.signal,
  });
}

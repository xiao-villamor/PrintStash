import { getJson, sendJson } from "@/lib/api/request";

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

export function getActiveGcPlan(): Promise<GcPlan | null> {
  return getJson<GcPlan | null>("/api/v1/admin/gc", { fresh: true });
}

export function createGcPlan(): Promise<GcPlan> {
  return sendJson<GcPlan>("/api/v1/admin/gc", "POST", {});
}

export function approveGcPlan(id: number, digest: string): Promise<GcPlan> {
  return sendJson<GcPlan>(`/api/v1/admin/gc/${id}/approve`, "POST", { digest });
}

export function abortGcPlan(id: number): Promise<GcPlan> {
  return sendJson<GcPlan>(`/api/v1/admin/gc/${id}/abort`, "POST", {});
}

export function finalizeGcPlan(id: number): Promise<GcPlan> {
  return sendJson<GcPlan>(`/api/v1/admin/gc/${id}/finalize`, "POST", {});
}

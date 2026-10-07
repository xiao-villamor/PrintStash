import { getJson, jsonHeaders, requestApi, sendJson, type GetJsonOptions } from "@/lib/api/request";
import type { DerivativeRead, WorkOverview, VaultConfigUpdate, VaultConfigRead } from "@/types";

/** Lanes, definitions, executors and recent failures (administrators only). */
export function getWorkOverview(options?: GetJsonOptions): Promise<WorkOverview> {
  return getJson<WorkOverview>("/api/v1/admin/work", options);
}

/** Override a lane's concurrency at runtime; `null` returns it to its default. */
export function setLaneConcurrency(
  lane: string,
  concurrency: number | null,
  options?: { signal?: AbortSignal },
): Promise<WorkOverview> {
  return requestApi<WorkOverview>(`/api/v1/admin/work/lanes/${encodeURIComponent(lane)}`, {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify({ concurrency }),
    signal: options?.signal,
  });
}

/** Withdraw every Job of one definition that has not started yet. */
export function cancelQueuedJobs(
  definition: string,
  options?: { signal?: AbortSignal },
): Promise<{ cancelled: number }> {
  return requestApi<{ cancelled: number }>("/api/v1/admin/work/cancel-queued", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ definition }),
    signal: options?.signal,
  });
}

/**
 * `missing` derives only what no attempt exists for yet; `all` re-derives every
 * Artifact's `kind`, keeping each current output visible until its replacement
 * is ready.
 */
export function regenerateDerivatives(
  kind: string,
  mode: "missing" | "all",
  options?: { signal?: AbortSignal },
): Promise<{ kind: string; mode: "missing" | "all" }> {
  // Settings still owns an unmigrated caller; remove this compatibility adapter in M10.
  return requestApi(`/api/v1/admin/work/derivatives/${encodeURIComponent(kind)}/regenerate`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ mode }),
    signal: options?.signal,
  });
}

export function listDerivatives(fileId: number): Promise<DerivativeRead[]> {
  return getJson<DerivativeRead[]>(`/api/v1/files/${fileId}/derivatives`, {});
}

export function retryDerivative(fileId: number, kind: string): Promise<DerivativeRead[]> {
  return sendJson<DerivativeRead[]>(
    `/api/v1/files/${fileId}/derivatives/${encodeURIComponent(kind)}/retry`,
    "POST",
    {},
  );
}

/** A one-use ticket for `/api/v1/events/ws`; an access token never goes in a URL. */
export function createEventsTicket(
  signal?: AbortSignal,
): Promise<{ ticket: string; expires_in: number }> {
  return requestApi("/api/v1/events/ticket", {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal,
  });
}

/** The Background work owner changes only derivative policy overrides. */
export type WorkDerivativePolicy = Pick<
  VaultConfigUpdate,
  "derivatives_mesh_enabled" | "derivatives_gcode_enabled" | "derivatives_toolpath_enabled"
>;

export async function updateWorkDerivativePolicy(
  body: WorkDerivativePolicy,
  options?: { signal?: AbortSignal },
): Promise<void> {
  await requestApi<VaultConfigRead>("/api/v1/config", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    signal: options?.signal,
  });
}

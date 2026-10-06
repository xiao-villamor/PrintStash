import { getJson, jsonHeaders, requestApi, sendJson } from "@/lib/api/request";
import type { DerivativeRead, WorkOverview } from "@/types";

/** Lanes, definitions, executors and recent failures (administrators only). */
export function getWorkOverview(): Promise<WorkOverview> {
  return getJson<WorkOverview>("/api/v1/admin/work", { fresh: true });
}

/** Override a lane's concurrency at runtime; `null` returns it to its default. */
export function setLaneConcurrency(
  lane: string,
  concurrency: number | null,
): Promise<WorkOverview> {
  return sendJson<WorkOverview>(`/api/v1/admin/work/lanes/${encodeURIComponent(lane)}`, "PUT", {
    concurrency,
  });
}

/** Withdraw every Job of one definition that has not started yet. */
export function cancelQueuedJobs(definition: string): Promise<{ cancelled: number }> {
  return sendJson<{ cancelled: number }>("/api/v1/admin/work/cancel-queued", "POST", {
    definition,
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
): Promise<{ kind: string; mode: "missing" | "all" }> {
  return sendJson(`/api/v1/admin/work/derivatives/${encodeURIComponent(kind)}/regenerate`, "POST", {
    mode,
  });
}

export function listDerivatives(fileId: number): Promise<DerivativeRead[]> {
  return getJson<DerivativeRead[]>(`/api/v1/files/${fileId}/derivatives`, { fresh: true });
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

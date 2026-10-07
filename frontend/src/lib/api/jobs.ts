import { getJson, sendJson, requestApi, jsonHeaders, type GetJsonOptions } from "@/lib/api/request";
import type { JobStatus } from "@/types";

/** One Job, uncached: a cached status never sees its Job finish. */
export function getJobStatus(jobId: string, options: GetJsonOptions = {}): Promise<JobStatus> {
  return getJson<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}`, {
    ...options,
  });
}

/**
 * The caller's active Jobs plus a bounded tail of finished ones. `trackedJobIds`
 * are always included whatever their age, so a browser that tracked a Job can
 * still observe how it ended after a reload.
 */
export function listJobs(
  trackedJobIds: string[] = [],
  options: GetJsonOptions = {},
): Promise<JobStatus[]> {
  const params = new URLSearchParams();
  trackedJobIds.forEach((jobId) => params.append("tracked_job_id", jobId));
  const query = params.size ? `?${params.toString()}` : "";
  return getJson<JobStatus[]>(`/api/v1/jobs${query}`, { ...options });
}

/** Administrator's live queue, including system-owned preview and maintenance Jobs. */
export function listWorkJobs(options?: GetJsonOptions): Promise<JobStatus[]> {
  return getJson<JobStatus[]>("/api/v1/jobs?include_system=true&terminal_limit=0", options);
}

/** Withdraw what the Job was doing (the server releases its subject first). */
export function cancelJob(jobId: string, options?: { signal?: AbortSignal }): Promise<JobStatus> {
  return requestApi<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options?.signal,
  });
}

/** Queue a failed or cancelled Job again on the same subject. */
export function retryJob(jobId: string, options?: { signal?: AbortSignal }): Promise<JobStatus> {
  return requestApi<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options?.signal,
  });
}

/** Release retained, uncommitted input after the user confirms. */
export function discardJobStaging(jobId: string): Promise<void> {
  return sendJson<void>(`/api/v1/jobs/${encodeURIComponent(jobId)}/discard-staging`, "POST", {});
}

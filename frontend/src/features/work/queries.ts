import { useCallback, useEffect, useSyncExternalStore } from "react";
import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getUser, onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, withSessionRequest } from "@/lib/session-transport";
import { subscribeEvents } from "@/lib/events";
import {
  getWorkOverview,
  setLaneConcurrency,
  cancelQueuedJobs,
  regenerateDerivatives,
  updateWorkDerivativePolicy,
  type WorkDerivativePolicy,
} from "@/lib/api/work";
import { listWorkJobs, cancelJob, retryJob } from "@/lib/api/jobs";
import type { DerivativeKind, JobStatus, WorkOverview, WorkLane, WorkDefinition } from "@/types";

export const workKeys = {
  all: ["admin-work"] as const,
  overview: ["admin-work", "overview"] as const,
  jobs: ["admin-work", "jobs"] as const,
};

/** Administrative work readers and acknowledgements; snapshots belong to Query. */
export interface BackgroundWorkApi {
  overview: typeof getWorkOverview;
  jobs: typeof listWorkJobs;
  setPolicy: typeof updateWorkDerivativePolicy;
  cancelJob: typeof cancelJob;
  setLane: typeof setLaneConcurrency;
  cancelQueued: typeof cancelQueuedJobs;
  regenerate: typeof regenerateDerivatives;
  retry: typeof retryJob;
}
export const workApi: BackgroundWorkApi = {
  overview: getWorkOverview,
  jobs: listWorkJobs,
  setPolicy: updateWorkDerivativePolicy,
  cancelJob,
  setLane: setLaneConcurrency,
  cancelQueued: cancelQueuedJobs,
  regenerate: regenerateDerivatives,
  retry: retryJob,
};

export function workOverviewOptions(api = workApi) {
  return queryOptions({
    queryKey: workKeys.overview,
    queryFn: ({ signal }) =>
      withSessionRequest((request) => api.overview({ signal: request.signal }), signal),
    staleTime: 10_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });
}
export function workJobsOptions(api = workApi) {
  return queryOptions({
    queryKey: workKeys.jobs,
    queryFn: ({ signal }) =>
      withSessionRequest((request) => api.jobs({ signal: request.signal }), signal),
    staleTime: 10_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });
}
export function useBackgroundWork(api = workApi) {
  useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const enabled = Boolean(getUser()?.is_superuser);
  const client = useQueryClient();
  const overview = useQuery({ ...workOverviewOptions(api), enabled });
  const jobs = useQuery({ ...workJobsOptions(api), enabled });
  const refresh = useCallback(async () => {
    await Promise.all(
      [workKeys.overview, workKeys.jobs].map((queryKey) =>
        client.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: false }),
      ),
    );
  }, [client]);
  useEffect(() => {
    if (!enabled) return;
    return subscribeEvents((notice) => {
      if (notice.type === "job" || notice.type === "resync" || notice.type === "derivative_policy")
        void refresh();
    });
  }, [enabled, refresh]);
  return {
    overview: enabled ? (overview.data ?? null) : null,
    activeJobs: enabled ? (jobs.data ?? []).filter(activeJob) : [],
    loading: enabled && (overview.isPending || jobs.isPending),
    error: enabled ? (overview.error ?? jobs.error) : null,
    refresh,
  };
}
function activeJob(job: JobStatus): boolean {
  return job.state === "queued" || job.state === "running" || job.state === "interrupted";
}

export type WorkChange = (
  | { kind: "lane"; lane: WorkLane["name"]; concurrency: number | null }
  | { kind: "policy"; body: WorkDerivativePolicy }
  | { kind: "cancel-job"; jobId: string }
  | { kind: "retry"; jobId: string }
  | { kind: "cancel-queued"; definition: WorkDefinition["name"] }
  | { kind: "regenerate"; derivative: DerivativeKind; mode: "missing" | "all" }
) & { signal?: AbortSignal };

export type WorkOutcome =
  | { kind: "cancel-queued"; cancelled: number }
  | { kind: "lane" | "policy" | "cancel-job" | "retry" | "regenerate" };

/** Reconcile acknowledgements after retiring reads that predate the write. */
export function useWorkMutation(api = workApi) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (change: WorkChange) =>
      withSessionRequest(async (request) => {
        let keys: readonly (readonly string[])[] = [workKeys.overview, workKeys.jobs];
        let publish = () => {};
        let outcome: WorkOutcome;
        const options = { signal: request.signal };
        switch (change.kind) {
          case "lane": {
            const saved = await api.setLane(change.lane, change.concurrency, options);
            request.assertCurrent();
            const lane = saved.lanes.find((item) => item.name === change.lane);
            if (!lane) throw new Error("work_lane_acknowledgement_missing");
            keys = [workKeys.overview];
            publish = () =>
              client.setQueryData<WorkOverview>(workKeys.overview, (previous) =>
                previous
                  ? {
                      ...previous,
                      lanes: previous.lanes.map((item) => (item.name === lane.name ? lane : item)),
                    }
                  : saved,
              );
            outcome = { kind: "lane" };
            break;
          }
          case "policy":
            await api.setPolicy(change.body, options);
            keys = [...keys, ["vault-config"]];
            outcome = { kind: "policy" };
            break;
          case "cancel-job":
          case "retry": {
            const saved =
              change.kind === "cancel-job"
                ? await api.cancelJob(change.jobId, options)
                : await api.retry(change.jobId, options);
            if (saved.job_id !== change.jobId) throw new Error("work_job_acknowledgement_mismatch");
            publish = () =>
              client.setQueryData<JobStatus[]>(workKeys.jobs, (previous) => {
                if (!previous) return undefined;
                const others = previous.filter((job) => job.job_id !== saved.job_id);
                return activeJob(saved) ? [...others, saved] : others;
              });
            outcome = { kind: change.kind };
            break;
          }
          case "cancel-queued": {
            const saved = await api.cancelQueued(change.definition, options);
            outcome = { kind: "cancel-queued", cancelled: saved.cancelled };
            break;
          }
          case "regenerate":
            await api.regenerate(change.derivative, change.mode, options);
            outcome = { kind: "regenerate" };
            break;
        }
        request.assertCurrent();
        await Promise.all(keys.map((queryKey) => client.cancelQueries({ queryKey, exact: true })));
        request.assertCurrent();
        publish();
        request.assertCurrent();
        await Promise.all(
          keys.map((queryKey) => client.invalidateQueries({ queryKey, exact: true })),
        );
        request.assertCurrent();
        return outcome;
      }, change.signal),
  });
}

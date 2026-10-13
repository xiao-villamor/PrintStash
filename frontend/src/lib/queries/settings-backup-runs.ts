/** Bounded backup execution history and exact-destination retry reconciliation. */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { queryOptions, useQueryClient } from "@tanstack/react-query";
import { listBackupRuns, retryBackupDestination } from "@/lib/api/backup";
import { useAuth } from "@/lib/auth-context";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError, parseApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { waitForImportJob } from "@/lib/task-center";
import type { JobStatus } from "@/types";

export const backupRunKeys = { all: ["backup-runs"] as const };
export function backupRunsOptions(reader: typeof listBackupRuns = listBackupRuns) {
  return queryOptions({
    queryKey: backupRunKeys.all,
    queryFn: ({ signal }) => reader({ signal }),
    retry: false,
    staleTime: 0,
  });
}
export interface BackupRetryCommand {
  session: number;
  destinationId: string;
  taskTitle: string;
}
type RetryState =
  | { status: "idle" | "success" }
  | { status: "pending"; destinationId: string }
  | { status: "error"; error: unknown };

/** Only the local command is disposed. Accepted work remains owned by TaskCenter. */
export function useBackupDestinationRetry() {
  const client = useQueryClient();
  const { user } = useAuth();
  const admin = useRef(!!user?.is_superuser);
  useLayoutEffect(() => {
    admin.current = !!user?.is_superuser;
  }, [user?.is_superuser]);
  const live = useRef(true);
  const active = useRef<AbortController | null>(null);
  const [state, setState] = useState<RetryState>({ status: "idle" });
  useEffect(() => {
    live.current = true;
    const release = onAuthChange(() => {
      active.current?.abort();
      active.current = null;
      setState({ status: "idle" });
    });
    return () => {
      live.current = false;
      active.current?.abort();
      active.current = null;
      release();
    };
  }, []);
  async function retry(command: BackupRetryCommand): Promise<JobStatus> {
    if (!live.current) throw new DOMException("Backup history view was disposed", "AbortError");
    if (active.current) throw new Error("A backup destination retry is already pending");
    requireSessionVersion(command.session);
    const controller = new AbortController();
    active.current = controller;
    setState({ status: "pending", destinationId: command.destinationId });
    function assertLocalCurrent() {
      requireSessionVersion(command.session);
      controller.signal.throwIfAborted();
      if (!live.current || active.current !== controller)
        throw new DOMException("Backup history view was disposed", "AbortError");
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
    }
    function isLocalCurrent() {
      return (
        live.current &&
        active.current === controller &&
        !controller.signal.aborted &&
        admin.current &&
        command.session === getSessionVersion()
      );
    }
    function assertEligible() {
      const projection = client.getQueryState(backupRunKeys.all);
      if (projection?.status === "error") throw parseApiError(projection.error);
      const runs = client.getQueryData<Awaited<ReturnType<typeof listBackupRuns>>>(
        backupRunKeys.all,
      );
      const run = runs?.find((row) =>
        row.destinations.some((destination) => destination.id === command.destinationId),
      );
      const destination = run?.destinations.find((row) => row.id === command.destinationId);
      if (!run || destination?.outcome !== "failed")
        throw new ApiError(409, "backup_retry_not_failed", "backup_retry_not_failed");
      if (run.outcome === "running")
        throw new ApiError(409, "backup_retry_backup_running", "backup_retry_backup_running");
      if (run.archive_sha256 === null)
        throw new ApiError(
          409,
          "backup_retry_new_backup_required",
          "backup_retry_new_backup_required",
        );
    }
    async function refreshHistory() {
      assertLocalCurrent();
      await client.cancelQueries({ queryKey: backupRunKeys.all, exact: true });
      assertLocalCurrent();
      await client.invalidateQueries(
        { queryKey: backupRunKeys.all, exact: true },
        { throwOnError: true },
      );
      assertLocalCurrent();
    }
    let dispatched = false;
    let refreshed = false;
    try {
      assertLocalCurrent();
      assertEligible();
      await client.cancelQueries({ queryKey: backupRunKeys.all, exact: true });
      assertLocalCurrent();
      assertEligible();
      dispatched = true;
      const accepted = await retryBackupDestination(command.destinationId, {
        signal: controller.signal,
      });
      // This receipt was decoded in its session. Local disposal must not discard known durable work.
      requireSessionVersion(command.session);
      if (!admin.current) throw new ApiError(403, "forbidden", "forbidden");
      const job = await waitForImportJob(accepted.job_id, command.taskTitle);
      assertLocalCurrent();
      refreshed = true;
      await refreshHistory();
      if (job.state !== "completed")
        throw new Error(job.error ?? "backup_retry_publication_failed");
      if (isLocalCurrent()) setState({ status: "success" });
      return job;
    } catch (error) {
      if (dispatched && !refreshed && isLocalCurrent()) {
        refreshed = true;
        try {
          await refreshHistory();
        } catch {
          /* The query exposes its own read failure; preserve the command error. */
        }
      }
      if (isLocalCurrent()) setState({ status: "error", error });
      throw error;
    } finally {
      if (active.current === controller) active.current = null;
    }
  }
  return {
    retry,
    retrying: state.status === "pending" ? state.destinationId : null,
    error: state.status === "error" ? state.error : null,
  };
}

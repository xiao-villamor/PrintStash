import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { getPreviewActivity } from "@/lib/api/work";
import { derivativeFailureCause, derivativeFailureDetail } from "@/lib/derivative-failures";
import { queryKeys } from "@/lib/query-client";
import { previewProgress } from "@/lib/preview-activity";
import { uiMessage, uiText } from "@/lib/locale";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { createTask, updateTask } from "@/lib/task-center";

const BUSY_MS = 4_000;
const IDLE_MS = 30_000;

/**
 * Shows image preparation across the library (covers and multi-angle sets)
 * as one Task, so an import or a scan that leaves hundreds of them behind is not
 * silent. Counts include the backlog before Jobs enter the bounded queue.
 * The Task is ambient: it is not kept across a reload, since nothing could
 * resume it.
 */
export function PreviewActivityTracker({ enabled }: { enabled: boolean }) {
  const startup = useLibraryStartup();
  const jobs = useQuery({
    queryKey: queryKeys.previewActivity,
    queryFn: ({ signal }) => getPreviewActivity({ signal }),
    enabled: enabled && startup.canLoad("activity"),
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data !== undefined && query.state.data.remaining > 0 ? BUSY_MS : IDLE_MS,
  });
  const run = useRef<{ id: string; failureBaseline: number | null } | null>(null);
  const lastObservedFailureAt = useRef<number | null | undefined>(undefined);

  useEffect(() => {
    if (!enabled || jobs.data === undefined) return;
    const { remaining, latest_failure: failure } = jobs.data;
    const failureAt = failure ? Date.parse(failure.at) : null;
    const previouslyObservedFailureAt = lastObservedFailureAt.current;
    lastObservedFailureAt.current = failureAt;
    const progress = previewProgress(jobs.data);
    if (remaining > 0) {
      const patch = {
        status: "running",
        ...progress,
      } as const;
      if (run.current === null) {
        run.current = {
          id: createTask({
            title: uiText("Preparing previews"),
            ambient: true,
            ...patch,
          }),
          // Counts cover the entire library, including failures from earlier runs.
          failureBaseline:
            previouslyObservedFailureAt === undefined ? failureAt : previouslyObservedFailureAt,
        };
      } else updateTask(run.current.id, patch);
    } else if (run.current !== null) {
      const { failureBaseline } = run.current;
      const newFailure =
        failureAt !== null && (failureBaseline === null || failureAt > failureBaseline)
          ? failure
          : null;
      const detail = newFailure
        ? derivativeFailureDetail(derivativeFailureCause(newFailure.reason))
        : progress.failed > 0
          ? uiMessage(
              "{completed} ready · {skipped} skipped · Earlier library failures ({failed})",
              { completed: progress.succeeded, skipped: progress.skipped, failed: progress.failed },
            )
          : progress.detail;
      updateTask(run.current.id, {
        ...progress,
        status: newFailure ? "failed" : "completed",
        detail,
      });
      run.current = null;
    }
  }, [enabled, jobs.data]);

  return null;
}

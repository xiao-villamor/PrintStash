import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { getPreviewActivity } from "@/lib/api/work";
import { queryKeys } from "@/lib/query-client";
import { previewProgress } from "@/lib/preview-activity";
import { uiText } from "@/lib/locale";
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
  const taskId = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || jobs.data === undefined) return;
    const { remaining, failed } = jobs.data;
    const progress = previewProgress(jobs.data);
    if (remaining > 0) {
      const patch = {
        status: "running",
        ...progress,
      } as const;
      if (taskId.current === null) {
        taskId.current = createTask({
          title: uiText("Preparing previews"),
          ambient: true,
          ...patch,
        });
      } else updateTask(taskId.current, patch);
    } else if (taskId.current !== null) {
      updateTask(taskId.current, {
        ...progress,
        status: failed > 0 ? "failed" : "completed",
      });
      taskId.current = null;
    }
  }, [enabled, jobs.data]);

  return null;
}

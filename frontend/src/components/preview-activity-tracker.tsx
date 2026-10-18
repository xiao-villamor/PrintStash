import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { listWorkJobs } from "@/lib/api/jobs";
import { previewJobsRemaining, previewProgress } from "@/lib/preview-activity";
import { uiText } from "@/lib/locale";
import { useLibraryStartup } from "@/lib/library-startup-context";
import { createTask, updateTask } from "@/lib/task-center";

const BUSY_MS = 4_000;
const IDLE_MS = 30_000;

/**
 * Shows the previews the server is preparing on its own (thumbnails, 3D views)
 * as one Task, so an import or a scan that leaves hundreds of them behind is not
 * silent. Administrators only: the live queue, with system-owned Jobs, is theirs.
 * The Task is ambient: it is not kept across a reload, since nothing could
 * resume it.
 */
export function PreviewActivityTracker({ enabled }: { enabled: boolean }) {
  const startup = useLibraryStartup();
  const jobs = useQuery({
    queryKey: ["work", "preview-activity"],
    queryFn: ({ signal }) => listWorkJobs({ signal }),
    enabled: enabled && startup.canLoad("activity"),
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data !== undefined && previewJobsRemaining(query.state.data) > 0
        ? BUSY_MS
        : IDLE_MS,
  });
  const taskId = useRef<string | null>(null);
  const peak = useRef(0);

  useEffect(() => {
    if (jobs.data === undefined) return;
    const remaining = previewJobsRemaining(jobs.data);
    if (remaining > 0) {
      const progress = previewProgress(peak.current, remaining);
      peak.current = progress.peak;
      const patch = {
        status: "running",
        total: progress.peak,
        processed: progress.done,
        progress: progress.percent,
        detail: uiText("{count} left · continues in background", { count: remaining }),
      } as const;
      if (taskId.current === null) {
        taskId.current = createTask({
          title: uiText("Preparing previews"),
          ambient: true,
          ...patch,
        });
      } else updateTask(taskId.current, patch);
    } else if (taskId.current !== null) {
      updateTask(taskId.current, { status: "completed", detail: uiText("Previews are ready") });
      taskId.current = null;
      peak.current = 0;
    }
  }, [jobs.data]);

  return null;
}

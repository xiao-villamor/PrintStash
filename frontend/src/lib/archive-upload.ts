import { inspectArchive } from "@/lib/api";
import { ApiError } from "@/lib/errors";
import { uiMessage, uiText } from "@/lib/locale";
import { attachTaskToImportJob, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";

const activeTransfers = new Map<string, AbortController>();

export function isArchiveTransferActive(taskId: string): boolean {
  return activeTransfers.has(taskId);
}

export function cancelArchiveTransfer(taskId: string): boolean {
  const controller = activeTransfers.get(taskId);
  if (!controller) return false;
  controller.abort();
  activeTransfers.delete(taskId);
  updateTask(taskId, {
    status: "failed",
    archiveUploading: false,
    detail: uiText("Upload cancelled"),
    progress: 100,
    retryable: false,
  });
  return true;
}

export async function startArchiveTransfer(taskId: string, file: File): Promise<void> {
  const controller = new AbortController();
  activeTransfers.set(taskId, controller);
  const startedAt = performance.now();
  try {
    const form = new FormData();
    form.append("file", file);
    const response = await inspectArchive(form, controller.signal, (loaded, total) => {
      if (controller.signal.aborted) return;
      const transferred = Math.min(file.size, loaded);
      const elapsedSeconds = (performance.now() - startedAt) / 1000;
      const speed = elapsedSeconds >= 0.5 && loaded > 0 ? loaded / elapsedSeconds : undefined;
      const complete = loaded >= total;
      updateTask(taskId, {
        status: "running",
        detail: complete
          ? uiText("Finishing transfer to PrintStash. ZIP preparation starts next.")
          : uiText("Transferring file"),
        progress: complete ? 95 : Math.round((loaded / total) * 90),
        archiveTransferredBytes: transferred,
        archiveSpeedBytesPerSecond: complete ? undefined : speed,
        archiveEtaSeconds: complete || !speed ? undefined : (total - loaded) / speed,
      });
    });
    if (controller.signal.aborted) return;
    updateTask(taskId, { title: uiMessage("Prepare {value1}", { value1: file.name }) });
    attachTaskToImportJob(taskId, response.job_id);
    toast.info(
      uiText("ZIP preparation continues in the background. We'll notify you when it's ready."),
    );
  } catch (error) {
    if (controller.signal.aborted) return;
    const detail =
      error instanceof ApiError
        ? error.code
        : error instanceof Error
          ? error.message
          : String(error);
    updateTask(taskId, {
      status: "failed",
      archiveUploading: false,
      progress: 100,
      detail,
      retryable: false,
    });
    toast.error(error);
  } finally {
    if (activeTransfers.get(taskId) === controller) activeTransfers.delete(taskId);
  }
}

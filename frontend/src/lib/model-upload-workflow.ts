/** Session-bound execution of accepted model uploads; modal disposal does not own this work. */
import { getModel } from "@/lib/api/models";
import { uploadArtifact, type ArtifactUploadProgress } from "@/lib/artifact-upload";
import { bulkTargetCollection, type BulkItem } from "@/lib/bulk-upload";
import { ApiError } from "@/lib/errors";
import { uiMessage, uiText } from "@/lib/locale";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { createTask, linkTaskToJob, updateTask, waitForImportJob } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import type { JobStatus } from "@/types";

export type ModelUploadFiles = { mesh: File; gcode: File | null } | { mesh: null; gcode: File };

async function waitForJob(
  session: number,
  jid: string,
  taskId: string,
  {
    progressEnd,
    completedDetail,
    completeTask,
  }: {
    progressEnd: number;
    completedDetail: string;
    completeTask: boolean;
  },
): Promise<JobStatus> {
  requireSessionVersion(session);
  const status = await waitForImportJob(jid);
  requireSessionVersion(session);
  if (status.state === "failed" || status.state === "cancelled") {
    throw new Error(status.error || "Ingestion job failed");
  }
  updateTask(taskId, {
    status: completeTask ? "completed" : "running",
    progress: completeTask ? 100 : progressEnd,
    detail: completedDetail,
  });
  return status;
}

export async function runModelUpload(
  {
    taskId,
    mesh,
    gcode,
    name,
    collection,
    tagsForUpload,
    libraryId,
    refreshAfter = true,
  }: {
    taskId: string;
    name: string;
    collection: string;
    tagsForUpload: string[];
    libraryId: number | "";
    refreshAfter?: boolean;
  } & ModelUploadFiles,
  onUploaded: () => Promise<void>,
  session = getSessionVersion(),
) {
  const reportProgress = (progress: ArtifactUploadProgress) => {
    if (session !== getSessionVersion()) return;
    const ratio = progress.totalBytes ? progress.transferredBytes / progress.totalBytes : 0;
    const phase = {
      hashing: { detail: uiText("Hashing file"), progress: 8 },
      transferring: {
        detail: uiText("Transferring file"),
        progress: 10 + Math.round(ratio * 55),
      },
      verifying: { detail: uiText("Verifying upload"), progress: 72 },
      ingesting: { detail: uiText("Processing upload"), progress: 80 },
      completed: { detail: uiText("Upload processed"), progress: 100 },
    }[progress.phase];
    updateTask(taskId, { ...phase, status: phase.progress === 100 ? "completed" : "running" });
  };
  const durableUpload = (file: File, purpose: "model" | "gcode", sourceHash?: string) =>
    uploadArtifact(
      file,
      {
        purpose,
        target_role: "new_model",
        model_name: name || file.name,
        collection: collection || undefined,
        tags: tagsForUpload.length ? tagsForUpload.join(",") : undefined,
        source_hash: sourceHash,
        target_library_id: libraryId === "" ? undefined : libraryId,
      },
      {
        onProgress: reportProgress,
        onSession: (uploadSessionId) =>
          updateTask(taskId, {
            uploadSessionId,
            uploadPaused: false,
            retryable: true,
          }),
      },
    );
  try {
    requireSessionVersion(session);
    if (mesh) {
      updateTask(taskId, {
        detail: uiMessage("Uploading {value1}", { value1: String(mesh.name) }),
        status: "running",
        progress: 15,
      });
      const meshRes = await durableUpload(mesh, "model");
      requireSessionVersion(session);
      if (!meshRes.job_id) throw new Error("Upload completed without an ingestion job");
      linkTaskToJob(taskId, meshRes.job_id);

      updateTask(taskId, {
        detail: uiMessage("Processing mesh and thumbnail"),
        status: "running",
        progress: 35,
      });
      const meshStatus = await waitForJob(session, meshRes.job_id, taskId, {
        progressEnd: gcode ? 55 : 100,
        completedDetail: gcode ? "Mesh processed; linking G-code" : "Upload processed",
        completeTask: !gcode,
      });

      if (!gcode) {
        requireSessionVersion(session);
        if (refreshAfter) await onUploaded();
        return;
      }

      if (meshStatus.model_id == null) {
        throw new Error("Mesh job completed but no model_id returned");
      }

      requireSessionVersion(session);
      const full = await getModel(meshStatus.model_id);
      requireSessionVersion(session);
      updateTask(taskId, {
        detail: uiMessage("Uploading {value1}", { value1: String(gcode.name) }),
        status: "running",
        progress: 60,
      });
      const gcodeRes = await durableUpload(gcode, "gcode", full.hash);
      requireSessionVersion(session);
      if (!gcodeRes.job_id) throw new Error("Upload completed without an ingestion job");
      linkTaskToJob(taskId, gcodeRes.job_id);
      await waitForJob(session, gcodeRes.job_id, taskId, {
        progressEnd: 100,
        completedDetail: "Upload processed",
        completeTask: true,
      });
      requireSessionVersion(session);
      if (refreshAfter) await onUploaded();
      return;
    }

    if (gcode) {
      updateTask(taskId, {
        detail: uiMessage("Uploading {value1}", { value1: String(gcode.name) }),
        status: "running",
        progress: 25,
      });
      const res = await durableUpload(gcode, "gcode");
      requireSessionVersion(session);
      if (!res.job_id) throw new Error("Upload completed without an ingestion job");
      linkTaskToJob(taskId, res.job_id);
      await waitForJob(session, res.job_id, taskId, {
        progressEnd: 100,
        completedDetail: "Upload processed",
        completeTask: true,
      });
      requireSessionVersion(session);
      if (refreshAfter) await onUploaded();
    }
  } catch (err: unknown) {
    if (session !== getSessionVersion()) return;
    if (err instanceof DOMException && err.name === "AbortError") {
      updateTask(taskId, {
        status: "running",
        uploadPaused: true,
        retryable: true,
        detail: uiText("Paused"),
      });
      return;
    }
    const msg =
      err instanceof ApiError ? err.code : err instanceof Error ? err.message : String(err);
    updateTask(taskId, {
      status: "failed",
      progress: 100,
      detail: msg,
    });
    toast.error(err);
  }
}

// Bulk: create one task per mesh upfront (so the whole queue is visible in
// the task center), then process sequentially to avoid hammering the vault
// with concurrent uploads. Each file becomes its own model — no G-code
// linking, which is what keeps this distinct from the "Files" tab.
export async function runBulkModelUpload(
  {
    files,
    collection,
    tagsForUpload,
    libraryId,
  }: {
    files: BulkItem[];
    collection: string;
    tagsForUpload: string[];
    libraryId: number | "";
  },
  onUploaded: () => Promise<void>,
) {
  const session = getSessionVersion();
  const queue = files.map((item) => ({
    item,
    taskId: createTask({
      title: uiMessage("Upload {value1}{value2}", {
        value1: String(item.relPath ? `${item.relPath}/` : ""),
        value2: String(item.file.name),
      }),
      detail: uiMessage("Queued"),
      status: "pending" as const,
      progress: 0,
      expectedJobCount: 1,
    }),
  }));
  try {
    for (const { item, taskId } of queue) {
      if (session !== getSessionVersion()) return;
      // Mirror the file's source folder into a nested collection under the
      // chosen base — the backend auto-creates intermediate collections.
      const targetCollection = bulkTargetCollection(collection, item.relPath);
      // runModelUpload owns its own error handling and marks the task failed,
      // so one bad file doesn't abort the rest of the queue.
      await runModelUpload(
        {
          taskId,
          mesh: item.file,
          gcode: null,
          name: item.file.name.replace(/\.[^/.]+$/, ""),
          collection: targetCollection,
          tagsForUpload,
          libraryId,
          refreshAfter: false,
        },
        onUploaded,
        session,
      );
    }
  } finally {
    // One invalidation after the entire queue, including partial failures.
    if (session === getSessionVersion()) {
      try {
        await onUploaded();
      } catch (error) {
        if (session === getSessionVersion()) toast.error(error);
      }
    }
  }
}

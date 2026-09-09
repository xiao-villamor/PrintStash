"use client";

import { ApiError, getErrorMessage } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import {
  cancelArtifactUpload,
  isArtifactUploadActive,
  pauseArtifactUpload,
  resumeArtifactUpload,
  type ArtifactUploadProgress,
} from "@/lib/artifact-upload";

import { CheckCircle2, ChevronDown, Loader2, XCircle } from "lucide-react";

import type { TaskItem } from "@/lib/task-center";
import { linkTaskToJob, taskTitle, taskDetail, updateTask } from "@/lib/task-center";
import { knownUiText } from "@/lib/locale";
import { Link } from "@/lib/link";

export function TaskList({
  tasks,
  onClear,
  compact = false,
}: {
  tasks: TaskItem[];
  onClear: () => void;
  compact?: boolean;
}) {
  useUiLocale();
  return (
    <div
      className={
        compact
          ? "w-full"
          : "w-[360px] max-w-[calc(100vw-2rem)] rounded border border-border bg-popover shadow-lg"
      }
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <span className="font-mono text-2xs uppercase tracking-wider text-muted-foreground">
          {uiText("Tasks")}
        </span>
        {tasks.some((task) => task.status === "completed" || task.status === "failed") && (
          <button
            type="button"
            onClick={onClear}
            className="rounded font-mono text-3xs uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {uiText("Clear done")}
          </button>
        )}
      </div>
      {tasks.length === 0 ? (
        <div className="px-4 py-8 text-center font-mono text-xs text-muted-foreground">
          {uiText("No active tasks")}
        </div>
      ) : (
        <div
          className={
            compact ? "max-h-64 overflow-y-auto py-2" : "max-h-[420px] overflow-y-auto py-2"
          }
        >
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
        </div>
      )}
    </div>
  );
}

function TaskRow({ task }: { task: TaskItem }) {
  useUiLocale();
  const active = (task.status === "pending" || task.status === "running") && !task.uploadPaused;
  return (
    <div className="px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5">
          {active ? (
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
          ) : task.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-success" />
          ) : (
            <XCircle className="h-4 w-4 text-destructive" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-medium text-foreground">{taskTitle(task)}</p>
            <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
              {task.uploadPaused ? uiText("Paused") : knownUiText(task.status)}
            </span>
          </div>
          {task.detail && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{taskDetail(task)}</p>
          )}
          {active && task.total == null && (
            <p className="mt-1 text-xs text-muted-foreground">
              {uiText("Discovering total… Safe to close this view.")}
            </p>
          )}
          <div className="mt-2 h-1.5 overflow-hidden rounded bg-muted">
            <div
              className={`h-full w-full origin-left transition-transform duration-slow ease-linear ${task.status === "failed" ? "bg-destructive" : "bg-primary"}`}
              style={{ transform: `scaleX(${Math.min(100, task.progress) / 100})` }}
            />
          </div>
          {!!task.failedItems?.length && (
            <details className="mt-2 text-xs text-muted-foreground">
              <summary className="flex cursor-pointer list-none items-center gap-1 font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <ChevronDown className="h-3.5 w-3.5" />
                {uiText(" Failed item details")}
              </summary>
              <ul className="mt-2 space-y-1">
                {task.failedItems.map((item, index) => (
                  <li key={`${item.name}-${index}`} className="break-words">
                    <span className="font-medium text-foreground">{item.name}</span>:{" "}
                    {getErrorMessage(item.reason)}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {task.retryable && !active && !task.uploadSessionId && (
            <button
              type="button"
              onClick={() => window.dispatchEvent(new CustomEvent("printstash:review-import"))}
              className="mt-2 rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {uiText("Review and retry")}
            </button>
          )}
          {task.uploadSessionId && task.status !== "completed" && <UploadControls task={task} />}
          {task.thumbnailStatus === "failed" && !active && (
            <Link
              href="/settings?section=maintenance"
              className="mt-2 inline-flex rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {uiText("Repair thumbnail")}
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

function uploadProgress(taskId: string, progress: ArtifactUploadProgress): void {
  const ratio = progress.totalBytes ? progress.transferredBytes / progress.totalBytes : 0;
  const phase = {
    hashing: { detail: uiText("Hashing file"), progress: 8 },
    transferring: { detail: uiText("Transferring file"), progress: 10 + Math.round(ratio * 55) },
    verifying: { detail: uiText("Verifying upload"), progress: 72 },
    ingesting: { detail: uiText("Processing upload"), progress: 80 },
    completed: { detail: uiText("Upload processed"), progress: 100 },
  }[progress.phase];
  updateTask(taskId, {
    ...phase,
    status: phase.progress === 100 ? "completed" : "running",
    uploadPaused: false,
  });
}

function UploadControls({ task }: { task: TaskItem }) {
  const sessionId = task.uploadSessionId;
  if (!sessionId) return null;
  const active = isArtifactUploadActive(sessionId) && !task.uploadPaused;

  const pause = () => {
    if (!pauseArtifactUpload(sessionId)) return;
    updateTask(task.id, {
      uploadPaused: true,
      detail: uiText("Paused"),
      retryable: true,
    });
  };
  const cancel = async () => {
    try {
      await cancelArtifactUpload(sessionId);
      updateTask(task.id, {
        status: "failed",
        progress: 100,
        detail: uiText("Upload cancelled"),
        retryable: false,
        uploadPaused: false,
        uploadSessionId: undefined,
      });
    } catch (error) {
      updateTask(task.id, {
        status: "failed",
        detail:
          error instanceof ApiError
            ? error.code
            : error instanceof Error
              ? error.message
              : String(error),
        retryable: true,
      });
    }
  };
  const resume = async (file: File) => {
    updateTask(task.id, {
      status: "running",
      detail: uiText("Transferring file"),
      uploadPaused: false,
    });
    try {
      const result = await resumeArtifactUpload(sessionId, file, {
        onProgress: (progress) => uploadProgress(task.id, progress),
      });
      if (result.job_id) linkTaskToJob(task.id, result.job_id);
    } catch (error) {
      const paused = error instanceof DOMException && error.name === "AbortError";
      updateTask(task.id, {
        status: paused ? "running" : "failed",
        detail: paused
          ? uiText("Paused")
          : error instanceof ApiError
            ? error.code
            : error instanceof Error
              ? error.message
              : String(error),
        uploadPaused: paused,
        retryable: true,
      });
    }
  };

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {active ? (
        <button
          type="button"
          onClick={pause}
          className="rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {uiText("Pause upload")}
        </button>
      ) : (
        <label className="cursor-pointer rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-within:outline-none focus-within:ring-2 focus-within:ring-ring">
          {uiText("Resume upload")}
          <input
            type="file"
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void resume(file);
              event.target.value = "";
            }}
          />
        </label>
      )}
      <button
        type="button"
        onClick={() => void cancel()}
        className="rounded border border-border px-2 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {uiText("Cancel upload")}
      </button>
    </div>
  );
}

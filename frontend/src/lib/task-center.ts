"use client";

import { listIngestJobs } from "@/lib/api/models";
import type { IngestJobStatus } from "@/types";

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface TaskItem {
  id: string;
  title: string;
  detail?: string;
  status: TaskStatus;
  progress: number;
  createdAt: number;
  updatedAt: number;
  jobId?: string;
  jobIds?: string[];
  expectedJobCount?: number;
  stage?: IngestJobStatus["stage"];
  processed?: number;
  total?: number | null;
  succeeded?: number;
  deduplicated?: number;
  skipped?: number;
  failed?: number;
  completion?: IngestJobStatus["completion"];
  retryable?: boolean;
  failedItems?: Array<{ name: string; reason: string; retryable: boolean }>;
}

const TASK_EVENT = "printstash:tasks-changed";
const STORAGE_KEY = "printstash:import-tasks:v1";
const DISMISSED_JOBS_KEY = "printstash:dismissed-import-jobs:v1";
let tasks: TaskItem[] = loadTasks();
const dismissedJobIds = loadDismissedJobIds();
let cleanupTimer: ReturnType<typeof setTimeout> | null = null;
let syncTimer: ReturnType<typeof setTimeout> | null = null;
let syncSubscribers = 0;
let syncFailures = 0;
let syncInFlight = false;
let syncWakePending = false;

function loadTasks(): TaskItem[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.slice(0, 20) : [];
  } catch {
    return [];
  }
}

function loadDismissedJobIds(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const parsed = JSON.parse(localStorage.getItem(DISMISSED_JOBS_KEY) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.slice(0, 200) : []);
  } catch {
    return new Set();
  }
}

function persist(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
}

function persistDismissedJobIds(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(
    DISMISSED_JOBS_KEY,
    JSON.stringify([...dismissedJobIds].slice(-200)),
  );
}

function emit() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(TASK_EVENT));
}

function clampProgress(progress: number): number {
  return Math.max(0, Math.min(100, Math.round(progress)));
}

function taskTtl(task: TaskItem): number | null {
  // Result summaries remain until user explicitly clears them.
  void task;
  return null;
}

function pruneExpired(now = Date.now()): boolean {
  const next = tasks.filter((task) => {
    const ttl = taskTtl(task);
    return ttl === null || now - task.updatedAt < ttl;
  });
  if (next.length === tasks.length) return false;
  tasks = next;
  return true;
}

function scheduleCleanup(): void {
  if (typeof window === "undefined") return;
  if (cleanupTimer) {
    clearTimeout(cleanupTimer);
    cleanupTimer = null;
  }

  const now = Date.now();
  const nextExpiry = tasks.reduce<number | null>((soonest, task) => {
    const ttl = taskTtl(task);
    if (ttl === null) return soonest;
    const expiresAt = task.updatedAt + ttl;
    return soonest === null ? expiresAt : Math.min(soonest, expiresAt);
  }, null);

  if (nextExpiry === null) return;
  cleanupTimer = setTimeout(() => {
    cleanupTimer = null;
    if (pruneExpired()) emit();
    scheduleCleanup();
  }, Math.max(0, nextExpiry - now));
}

export function listTasks(): TaskItem[] {
  if (pruneExpired()) {
    emit();
    scheduleCleanup();
  }
  return [...tasks].sort((a, b) => b.updatedAt - a.updatedAt);
}

export function createTask(
  input: Pick<TaskItem, "title"> &
    Partial<Omit<TaskItem, "id" | "title" | "createdAt" | "updatedAt">>,
): string {
  const now = Date.now();
  const id = `${now}-${Math.random().toString(36).slice(2, 8)}`;
  tasks = [
    {
      id,
      ...input,
      status: input.status ?? "pending",
      progress: clampProgress(input.progress ?? 0),
      createdAt: now,
      updatedAt: now,
    },
    ...tasks,
  ].slice(0, 20);
  persist();
  emit();
  scheduleCleanup();
  return id;
}

export function updateTask(
  id: string,
  patch: Partial<Omit<TaskItem, "id" | "createdAt" | "updatedAt">>,
): void {
  const now = Date.now();
  tasks = tasks.map((task) =>
    task.id === id
      ? {
          ...task,
          ...patch,
          progress:
            patch.progress === undefined
              ? task.progress
              : clampProgress(patch.progress),
          ...(patch.status === "completed" ? { progress: 100 } : {}),
          updatedAt: now,
        }
      : task,
  );
  persist();
  emit();
  scheduleCleanup();
}

export function linkTaskToJob(taskId: string, jobId: string): void {
  const task = tasks.find((item) => item.id === taskId);
  if (!task) return;
  const jobIds = [...new Set([...(task.jobIds ?? []), jobId])];
  dismissedJobIds.delete(jobId);
  persistDismissedJobIds();
  updateTask(taskId, { jobIds });
  wakeImportJobSync();
}

export function clearCompletedTasks(): void {
  for (const task of tasks) {
    if (task.status !== "completed" && task.status !== "failed") continue;
    if (task.jobId) dismissedJobIds.add(task.jobId);
    for (const jobId of task.jobIds ?? []) dismissedJobIds.add(jobId);
  }
  tasks = tasks.filter(
    (task) => task.status !== "completed" && task.status !== "failed",
  );
  persistDismissedJobIds();
  persist();
  emit();
  scheduleCleanup();
}

function detailForJob(job: IngestJobStatus): string {
  const stage = job.stage?.replaceAll("_", " ") ?? job.state;
  const count = job.total == null ? "" : ` ${job.processed ?? 0}/${job.total}`;
  const item = job.current_item ? ` · ${job.current_item}` : "";
  if (job.state === "completed") {
    return `${job.succeeded ?? 0} succeeded, ${job.deduplicated ?? 0} deduplicated, ${job.skipped ?? 0} skipped, ${job.failed ?? 0} failed`;
  }
  if (job.state === "failed") return job.error ?? "Import failed before anything was added";
  return `${stage}${count}${item} · continues in background`;
}

function applyJob(job: IngestJobStatus): void {
  if (dismissedJobIds.has(job.job_id)) return;
  const existing = tasks.find(
    (task) => task.jobId === job.job_id || task.jobIds?.includes(job.job_id),
  );
  if (existing && existing.status === job.state && (job.state === "completed" || job.state === "failed")) return;
  const status: TaskStatus = job.state;
  const patch = {
    jobId: job.job_id,
    status,
    progress: job.progress ?? (job.total ? ((job.processed ?? 0) / job.total) * 100 : 0),
    detail: detailForJob(job),
    stage: job.stage,
    processed: job.processed,
    total: job.total,
    succeeded: job.succeeded,
    deduplicated: job.deduplicated,
    skipped: job.skipped,
    failed: job.failed,
    completion: job.completion,
    retryable: job.retryable,
    failedItems: job.failed_items,
  };
  if (existing) updateTask(existing.id, patch);
  else createTask({ title: "Import", ...patch });
}

function applyGroupedJobs(task: TaskItem, jobs: IngestJobStatus[]): void {
  const expected = Math.max(task.expectedJobCount ?? task.jobIds?.length ?? 1, 1);
  const failedJob = jobs.find((job) => job.state === "failed");
  if (failedJob) {
    updateTask(task.id, {
      status: "failed",
      progress: 100,
      detail: failedJob.error ?? "Upload failed",
    });
    return;
  }

  const allCompleted =
    jobs.length >= expected && jobs.every((job) => job.state === "completed");
  if (allCompleted) {
    if (task.status !== "completed") {
      updateTask(task.id, {
        status: "completed",
        progress: 100,
        detail: expected === 1 ? "Upload processed" : `${expected} files processed`,
      });
    }
    return;
  }

  const current = [...jobs]
    .reverse()
    .find((job) => job.state === "running" || job.state === "pending");
  const completedProgress = jobs.reduce(
    (sum, job) => sum + (job.state === "completed" ? 100 : (job.progress ?? 0)),
    0,
  );
  updateTask(task.id, {
    status: jobs.some((job) => job.state === "running" || job.state === "completed")
      ? "running"
      : "pending",
    progress: completedProgress / expected,
    detail: current ? detailForJob(current) : task.detail,
  });
}

export function trackImportJob(jobId: string, title: string): string {
  const existing = tasks.find((task) => task.jobId === jobId);
  if (existing) return existing.id;
  const taskId = createTask({
    title,
    detail: "Queued · continues in background",
    status: "pending",
    progress: 0,
    jobId,
  });
  wakeImportJobSync();
  return taskId;
}

export async function syncImportJobs(): Promise<boolean> {
  const jobs = (await listIngestJobs()).filter(
    (job) => !dismissedJobIds.has(job.job_id),
  );
  const jobsById = new Map(jobs.map((job) => [job.job_id, job]));
  const claimedJobIds = new Set<string>();

  for (const task of tasks) {
    if (!task.jobIds?.length) continue;
    const groupedJobs = task.jobIds
      .map((jobId) => jobsById.get(jobId))
      .filter((job): job is IngestJobStatus => job !== undefined);
    task.jobIds.forEach((jobId) => claimedJobIds.add(jobId));
    if (groupedJobs.length) applyGroupedJobs(task, groupedJobs);
  }

  jobs.filter((job) => !claimedJobIds.has(job.job_id)).forEach(applyJob);
  return jobs.some((job) => job.state === "pending" || job.state === "running");
}

function hasTrackedActiveJobs(): boolean {
  return tasks.some(
    (task) =>
      (task.status === "pending" || task.status === "running") &&
      (!!task.jobId || !!task.jobIds?.length),
  );
}

function clearSyncTimer(): void {
  if (syncTimer !== null) {
    clearTimeout(syncTimer);
    syncTimer = null;
  }
}

function scheduleImportJobSync(delay: number): void {
  if (
    typeof window === "undefined" ||
    syncSubscribers === 0 ||
    document.visibilityState === "hidden"
  ) {
    return;
  }
  clearSyncTimer();
  syncTimer = setTimeout(() => {
    syncTimer = null;
    void pollImportJobs();
  }, delay);
}

async function pollImportJobs(): Promise<void> {
  if (syncInFlight || syncSubscribers === 0 || document.visibilityState === "hidden") {
    return;
  }
  syncInFlight = true;
  try {
    const serverHasActiveJobs = await syncImportJobs();
    syncFailures = 0;
    if (serverHasActiveJobs || hasTrackedActiveJobs()) {
      scheduleImportJobSync(1_000);
    }
  } catch {
    syncFailures += 1;
    // We could not learn whether the server has active work. Retry at a
    // bounded backoff even when this browser has no local task record.
    scheduleImportJobSync(
      Math.min(30_000, 1_000 * 2 ** Math.max(0, syncFailures - 1)),
    );
  } finally {
    syncInFlight = false;
    if (syncWakePending) {
      syncWakePending = false;
      scheduleImportJobSync(0);
    }
  }
}

function wakeImportJobSync(): void {
  if (syncSubscribers === 0) return;
  if (syncInFlight) {
    syncWakePending = true;
    return;
  }
  scheduleImportJobSync(0);
}

function onVisibilityChange(): void {
  if (document.visibilityState === "hidden") clearSyncTimer();
  else scheduleImportJobSync(0);
}

export function startImportJobSync(): () => void {
  if (typeof window === "undefined") return () => {};
  syncSubscribers += 1;
  if (syncSubscribers === 1) {
    document.addEventListener("visibilitychange", onVisibilityChange);
    scheduleImportJobSync(0);
  }
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    syncSubscribers = Math.max(0, syncSubscribers - 1);
    if (syncSubscribers === 0) {
      clearSyncTimer();
      syncWakePending = false;
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  };
}

export function subscribeTasks(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(TASK_EVENT, callback);
  return () => window.removeEventListener(TASK_EVENT, callback);
}

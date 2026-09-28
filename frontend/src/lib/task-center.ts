"use client";

import { getErrorMessage } from "./errors";

import { listJobs } from "@/lib/api/jobs";
import { subscribeEvents } from "@/lib/events";
import type { JobState, JobStatus } from "@/types";
import { uiText, knownUiText, uiMessage, type MessageDescriptor } from "./locale";

export type TaskStatus = "pending" | "running" | "completed" | "failed";

/** How the Task Center reads the server's list of the user's Jobs. */
export type JobSource = (trackedJobIds: string[]) => Promise<JobStatus[]>;

let jobSource: JobSource = listJobs;

/**
 * Point the Task Center at a different job source. Production keeps the default
 * (`listJobs`); tests inject a stub so the sync state machine can be driven
 * without a network.
 */
export function setJobSource(source: JobSource): void {
  jobSource = source;
}

/**
 * A Job's state as a task row shows it. `interrupted` is a Job about to run
 * again after its process stopped, so it reads as waiting, not failed; a
 * cancelled Job is finished without a result.
 */
export function taskStatusOf(state: JobState): TaskStatus {
  switch (state) {
    case "queued":
    case "interrupted":
      return "pending";
    case "running":
      return "running";
    case "completed":
      return "completed";
    case "failed":
    case "cancelled":
      return "failed";
  }
}

function titleForJob(job: JobStatus): MessageDescriptor | string {
  if (job.kind === "ingestion.archive_inspect") return uiMessage("Prepare ZIP");
  if (job.kind.startsWith("ingestion.")) {
    return uiMessage("Import");
  }
  return job.label ?? job.kind;
}

export interface TaskItem {
  id: string;
  title: string;
  detail?: string;
  titleMessage?: MessageDescriptor;
  detailMessage?: MessageDescriptor;
  status: TaskStatus;
  progress: number;
  createdAt: number;
  updatedAt: number;
  jobId?: string;
  jobKind?: JobStatus["kind"];
  jobIds?: string[];
  expectedJobCount?: number;
  stage?: JobStatus["stage"];
  processed?: number;
  total?: number | null;
  succeeded?: number;
  deduplicated?: number;
  skipped?: number;
  failed?: number;
  completion?: JobStatus["completion"];
  /** The Job's own state; `interrupted` and `cancelled` refine `status`. */
  jobState?: JobState;
  currentItem?: string | null;
  error?: string | null;
  serverUpdatedAt?: string | null;
  retryable?: boolean;
  uploadSessionId?: string;
  uploadPaused?: boolean;
  failedItems?: Array<{ name: string; reason: string; retryable: boolean }>;
  archiveCollection?: string | null;
  archiveTags?: string[];
  archiveReviewDone?: boolean;
  archiveUploading?: boolean;
  archiveSizeBytes?: number;
  archiveTransferredBytes?: number;
  archiveSpeedBytesPerSecond?: number;
  archiveEtaSeconds?: number;
}

export function needsArchiveReview(task: TaskItem): boolean {
  return (
    task.jobKind === "ingestion.archive_inspect" &&
    task.status === "completed" &&
    !task.archiveReviewDone &&
    task.jobId !== undefined
  );
}

const TASK_EVENT = "printstash:tasks-changed";
const STORAGE_KEY = "printstash:import-tasks:v1";
const DISMISSED_JOBS_KEY = "printstash:dismissed-import-jobs:v1";
const TERMINAL_EVENT = "printstash:import-job-terminal";
const EMITTED_TERMINALS_KEY = "printstash:emitted-import-terminals:v1";

declare global {
  interface WindowEventMap {
    /** Dispatched by `publishTerminal` with the job that reached a terminal state. */
    [TERMINAL_EVENT]: CustomEvent<JobStatus>;
  }
}

/**
 * True when this module runs against a real DOM. Task Center state lives in
 * `localStorage` and `window` events, so every entry point that touches either
 * checks this first and degrades to a no-op during server-side rendering.
 */
const isBrowser = (): boolean => "window" in globalThis;

let tasks: TaskItem[] = loadTasks();
const dismissedJobIds = loadDismissedJobIds();
const emittedTerminalJobIds = loadIdSet(EMITTED_TERMINALS_KEY);
const terminalJobs = new Map<string, JobStatus>();
interface TerminalWaiter {
  resolve: (job: JobStatus) => void;
  reject: (error: Error) => void;
}
const terminalWaiters = new Map<string, Set<TerminalWaiter>>();

/** The server no longer reports a Job this browser was waiting on. */
export class JobStatusUnavailableError extends Error {
  constructor(readonly jobId: string) {
    super("job_status_unavailable");
    this.name = "JobStatusUnavailableError";
  }
}
let cleanupTimer: ReturnType<typeof setTimeout> | null = null;
let syncTimer: ReturnType<typeof setTimeout> | null = null;
let syncSubscribers = 0;
let syncFailures = 0;
let syncInFlight = false;
let syncWakePending = false;

function loadTasks(): TaskItem[] {
  if (!isBrowser()) return [];
  try {
    // Only `persist()` writes this key, so the stored payload is a TaskItem[]
    // snapshot; a hand-edited or truncated value falls through to the catch.
    const parsed: TaskItem[] | null = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? keepVisibleTasks(parsed).map((task) =>
          task.archiveUploading && (task.status === "pending" || task.status === "running")
            ? {
                ...task,
                archiveUploading: false,
                status: "failed" as const,
                detail: uiText("ZIP upload interrupted. Select the file again."),
                progress: 100,
              }
            : task,
        )
      : [];
  } catch {
    return [];
  }
}

function keepVisibleTasks(items: TaskItem[]): TaskItem[] {
  let ordinary = 0;
  return items.filter((item) => needsArchiveReview(item) || ordinary++ < 20);
}

function loadDismissedJobIds(): Set<string> {
  return loadIdSet(DISMISSED_JOBS_KEY);
}

function loadIdSet(key: string): Set<string> {
  if (!isBrowser()) return new Set();
  try {
    // Written only by `persistIdSet`, which stores an array of job ids.
    const parsed: string[] | null = JSON.parse(localStorage.getItem(key) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.slice(0, 200) : []);
  } catch {
    return new Set();
  }
}

function persistIdSet(key: string, values: Set<string>): void {
  if (!isBrowser()) return;
  localStorage.setItem(key, JSON.stringify([...values].slice(-200)));
}

function persist(): void {
  if (!isBrowser()) return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
}

function persistDismissedJobIds(): void {
  persistIdSet(DISMISSED_JOBS_KEY, dismissedJobIds);
}

function emit() {
  if (!isBrowser()) return;
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
  if (!isBrowser()) return;
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
  cleanupTimer = setTimeout(
    () => {
      cleanupTimer = null;
      if (pruneExpired()) emit();
      scheduleCleanup();
    },
    Math.max(0, nextExpiry - now),
  );
}

export function listTasks(): TaskItem[] {
  if (pruneExpired()) {
    emit();
    scheduleCleanup();
  }
  return [...tasks].sort((a, b) => b.updatedAt - a.updatedAt);
}

type TaskText = string | MessageDescriptor;
type TaskPatch = Partial<Omit<TaskItem, "id" | "title" | "detail" | "createdAt" | "updatedAt">> & {
  title?: TaskText;
  detail?: TaskText;
};

function taskTextPatch(input: TaskPatch): Partial<TaskItem> {
  const { title, detail, ...rest } = input;
  if (title !== undefined) {
    rest.titleMessage = title instanceof Object ? title : undefined;
  }
  if (detail !== undefined) {
    rest.detailMessage = detail instanceof Object ? detail : undefined;
  }
  const result: Partial<TaskItem> = { ...rest };
  if (title !== undefined)
    result.title = title instanceof Object ? uiText(title.key, title.values) : title;
  if (detail !== undefined)
    result.detail = detail instanceof Object ? uiText(detail.key, detail.values) : detail;
  return result;
}

export function taskTitle(task: TaskItem): string {
  return task.titleMessage
    ? uiText(task.titleMessage.key, task.titleMessage.values)
    : knownUiText(task.title);
}

export function taskDetail(task: TaskItem): string | undefined {
  if (task.detailMessage) return uiText(task.detailMessage.key, task.detailMessage.values);
  if (task.archiveReviewDone) return uiText("Files selected for import");
  if (task.status === "failed" && task.error) return getErrorMessage(task.error);
  if ((task.jobId || task.jobIds?.length) && task.stage && task.status !== "failed") {
    return detailForJob({
      job_id: task.jobId,
      kind: task.jobKind,
      state: task.jobState ?? (task.status === "pending" ? "queued" : task.status),
      stage: task.stage,
      processed: task.processed,
      total: task.total,
      succeeded: task.succeeded,
      deduplicated: task.deduplicated,
      skipped: task.skipped,
      failed: task.failed,
      completion: task.completion,
      current_item: task.currentItem,
      error: task.error,
    });
  }
  return task.detail
    ? task.status === "failed"
      ? getErrorMessage(task.detail)
      : knownUiText(task.detail)
    : undefined;
}

export function createTask(input: TaskPatch & { title: TaskText }): string {
  const now = Date.now();
  const id = `${now}-${Math.random().toString(36).slice(2, 8)}`;
  tasks = keepVisibleTasks([
    {
      id,
      ...taskTextPatch(input),
      title:
        input.title instanceof Object ? uiText(input.title.key, input.title.values) : input.title,
      status: input.status ?? "pending",
      progress: clampProgress(input.progress ?? 0),
      createdAt: now,
      updatedAt: now,
    },
    ...tasks,
  ]);
  persist();
  emit();
  scheduleCleanup();
  return id;
}

export function updateTask(id: string, patch: TaskPatch): void {
  const now = Date.now();
  tasks = tasks.map((task) => {
    if (task.id !== id) return task;
    const next: TaskItem = { ...task, ...taskTextPatch(patch), updatedAt: now };
    next.progress = patch.progress === undefined ? task.progress : clampProgress(patch.progress);
    // A completed task always reads as fully done, whatever progress it reported.
    if (patch.status === "completed") next.progress = 100;
    return next;
  });
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

/** Attach a one-job import to an existing browser task without losing its review metadata. */
export function attachTaskToImportJob(taskId: string, jobId: string): void {
  dismissedJobIds.delete(jobId);
  persistDismissedJobIds();
  updateTask(taskId, {
    jobId,
    archiveUploading: false,
    archiveSpeedBytesPerSecond: undefined,
    archiveEtaSeconds: undefined,
    status: "pending",
    detail: uiText("Queued · continues in background"),
  });
  wakeImportJobSync();
}

export function clearCompletedTasks(): void {
  for (const task of tasks) {
    if ((task.status !== "completed" && task.status !== "failed") || needsArchiveReview(task))
      continue;
    if (task.jobId) dismissedJobIds.add(task.jobId);
    for (const jobId of task.jobIds ?? []) dismissedJobIds.add(jobId);
  }
  tasks = tasks.filter((task) =>
    task.status !== "completed" && task.status !== "failed" ? true : needsArchiveReview(task),
  );
  persistDismissedJobIds();
  persist();
  emit();
  scheduleCleanup();
}

function detailForJob(job: Pick<JobStatus, "state"> & Partial<JobStatus>): string {
  if (job.state === "cancelled") return uiText("Cancelled");
  if (job.state === "interrupted") return uiText("Interrupted · resumes automatically");
  const stage = knownUiText(job.stage ?? (job.state === "queued" ? "pending" : job.state));
  const count = job.total == null ? "" : ` ${job.processed ?? 0}/${job.total}`;
  const item = job.current_item ? ` · ${job.current_item}` : "";
  if (job.state === "completed") {
    if (job.kind === "ingestion.archive_inspect") return uiText("Ready to choose ZIP files");
    if (job.completion === "partial") {
      return uiText("{succeeded} succeeded · {failed} failed", {
        succeeded: job.succeeded ?? 0,
        failed: job.failed ?? 0,
      });
    }
    return uiText(
      "{succeeded} succeeded, {deduplicated} deduplicated, {skipped} skipped, {failed} failed",
      {
        succeeded: job.succeeded ?? 0,
        deduplicated: job.deduplicated ?? 0,
        skipped: job.skipped ?? 0,
        failed: job.failed ?? 0,
      },
    );
  }
  if (job.state === "failed")
    return job.error
      ? getErrorMessage(job.error)
      : uiText("Import failed before anything was added");
  return uiText("{stage}{count}{item} · continues in background", { stage, count, item });
}

function isTerminal(job: JobStatus): boolean {
  return job.state === "completed" || job.state === "failed" || job.state === "cancelled";
}

function isActive(job: JobStatus): boolean {
  return !isTerminal(job);
}

function publishTerminal(job: JobStatus): void {
  if (!isTerminal(job)) return;
  terminalJobs.set(job.job_id, job);
  for (const waiter of terminalWaiters.get(job.job_id) ?? []) waiter.resolve(job);
  terminalWaiters.delete(job.job_id);
  if (emittedTerminalJobIds.has(job.job_id) || !isBrowser()) return;
  emittedTerminalJobIds.add(job.job_id);
  persistIdSet(EMITTED_TERMINALS_KEY, emittedTerminalJobIds);
  window.dispatchEvent(new CustomEvent<JobStatus>(TERMINAL_EVENT, { detail: job }));
}

/** A Job that vanished has no status to report: its waiters fail, nobody is told it completed. */
function rejectLostJob(jobId: string): void {
  for (const waiter of terminalWaiters.get(jobId) ?? []) {
    waiter.reject(new JobStatusUnavailableError(jobId));
  }
  terminalWaiters.delete(jobId);
}

function applyJob(job: JobStatus): void {
  if (dismissedJobIds.has(job.job_id)) return;
  const existing = tasks.find(
    (task) => task.jobId === job.job_id || task.jobIds?.includes(job.job_id),
  );
  // The reconnect endpoint includes a bounded terminal history so a locally
  // tracked job can still observe completion after a reload. Do not turn that
  // history into new Task Center rows: repeated syncs could otherwise evict
  // freshly queued browser-local uploads before they receive their job IDs.
  // Unknown active jobs are still discovered below and become reconnectable.
  if (!existing && isTerminal(job)) return;
  if (existing?.status === "completed" || existing?.status === "failed") {
    if (isTerminal(job)) publishTerminal(job);
    return;
  }
  if (
    existing?.serverUpdatedAt &&
    job.updated_at &&
    Date.parse(job.updated_at) < Date.parse(existing.serverUpdatedAt)
  )
    return;
  const status = taskStatusOf(job.state);
  const patch = {
    jobId: job.job_id,
    jobKind: job.kind,
    status,
    jobState: job.state,
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
    currentItem: job.current_item,
    error: job.error,
    serverUpdatedAt: job.updated_at,
  };
  if (existing) updateTask(existing.id, patch);
  else createTask({ title: titleForJob(job), ...patch });
  publishTerminal(job);
}

function applyGroupedJobs(task: TaskItem, jobs: JobStatus[]): void {
  jobs.forEach(publishTerminal);
  if (task.status === "completed" || task.status === "failed") return;
  const expected = Math.max(task.expectedJobCount ?? task.jobIds?.length ?? 1, 1);
  const failedJob = jobs.find((job) => job.state === "failed" || job.state === "cancelled");
  if (failedJob) {
    updateTask(task.id, {
      status: "failed",
      progress: 100,
      detail: failedJob.state === "cancelled" ? "Cancelled" : (failedJob.error ?? "Upload failed"),
    });
    return;
  }

  const allCompleted = jobs.length >= expected && jobs.every((job) => job.state === "completed");
  if (allCompleted) {
    updateTask(task.id, {
      status: "completed",
      progress: 100,
      detail:
        expected === 1
          ? uiMessage("Upload processed")
          : uiMessage("files.processed", { count: expected }),
    });
    return;
  }

  const current = [...jobs].reverse().find(isActive);
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
    stage: current?.stage,
    currentItem: current?.current_item,
    processed: current?.processed,
    total: current?.total,
  });
}

export function trackImportJob(jobId: string, title: TaskText): string {
  const existing = tasks.find((task) => task.jobId === jobId || task.jobIds?.includes(jobId));
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

export function waitForImportJob(
  jobId: string,
  title = "Import",
  timeoutMs = 15 * 60_000,
): Promise<JobStatus> {
  trackImportJob(jobId, title);
  const known = terminalJobs.get(jobId);
  if (known) return Promise.resolve(known);
  return new Promise((resolve, reject) => {
    const stopSync = startImportJobSync();
    const settle = () => {
      clearTimeout(timeout);
      stopSync();
    };
    const waiter: TerminalWaiter = {
      resolve: (job) => {
        settle();
        resolve(job);
      },
      reject: (error) => {
        settle();
        reject(error);
      },
    };
    const timeout = setTimeout(() => {
      terminalWaiters.get(jobId)?.delete(waiter);
      stopSync();
      reject(new Error("Timed out waiting for ingestion to complete"));
    }, timeoutMs);
    const waiters = terminalWaiters.get(jobId) ?? new Set();
    waiters.add(waiter);
    terminalWaiters.set(jobId, waiters);
    void syncImportJobs().catch(() => wakeImportJobSync());
  });
}

function reconcileLinkedJobDuplicates(): void {
  const groupedOwners = new Map<string, string>();
  for (const task of tasks) {
    for (const jobId of task.jobIds ?? []) groupedOwners.set(jobId, task.id);
  }
  const next = tasks.filter(
    (task) =>
      task.jobId === undefined ||
      groupedOwners.get(task.jobId) === undefined ||
      groupedOwners.get(task.jobId) === task.id,
  );
  if (next.length === tasks.length) return;
  tasks = next;
  persist();
  emit();
}

export async function syncImportJobs(): Promise<boolean> {
  // Older clients created a generic server-job row even after the same job had
  // been linked to its user-facing upload task. The grouped owner is the richer
  // record; remove the duplicate before claiming jobs so persisted stuck rows
  // repair themselves on the first sync after an upgrade.
  reconcileLinkedJobDuplicates();
  const trackedJobIds = [
    ...new Set(
      tasks
        .filter((task) => task.status === "pending" || task.status === "running")
        .flatMap((task) => task.jobIds ?? (task.jobId ? [task.jobId] : [])),
    ),
  ].slice(0, 20);
  const requestedJobIds = new Set(trackedJobIds);
  const jobs = (await jobSource(trackedJobIds)).filter((job) => !dismissedJobIds.has(job.job_id));
  const jobsById = new Map(jobs.map((job) => [job.job_id, job]));
  const claimedJobIds = new Set<string>();

  for (const task of tasks) {
    if (!task.jobIds?.length) continue;
    const groupedJobs = task.jobIds
      .map((jobId) => jobsById.get(jobId))
      .filter((job): job is JobStatus => job !== undefined);
    task.jobIds.forEach((jobId) => claimedJobIds.add(jobId));
    if (groupedJobs.length) applyGroupedJobs(task, groupedJobs);
  }

  jobs.filter((job) => !claimedJobIds.has(job.job_id)).forEach(applyJob);

  const unavailableDetail =
    "Task status is no longer available. It may have finished while this browser was disconnected.";
  for (const task of tasks) {
    if (task.status !== "pending" && task.status !== "running") continue;
    const linkedJobIds = task.jobIds ?? (task.jobId ? [task.jobId] : []);
    const missingJobIds = linkedJobIds.filter(
      (jobId) => requestedJobIds.has(jobId) && !jobsById.has(jobId),
    );
    if (!missingJobIds.length) continue;
    updateTask(task.id, {
      status: "failed",
      progress: 100,
      detail: unavailableDetail,
      retryable: true,
    });
    for (const jobId of missingJobIds) rejectLostJob(jobId);
  }
  return jobs.some(isActive);
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
  if (!isBrowser() || syncSubscribers === 0 || document.visibilityState === "hidden") {
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
    scheduleImportJobSync(Math.min(30_000, 1_000 * 2 ** Math.max(0, syncFailures - 1)));
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

function onConnectivityRestored(): void {
  scheduleImportJobSync(0);
}

let stopEvents: (() => void) | null = null;

/**
 * Keep the Task Center in step with the server while anyone shows it. The
 * events socket wakes a sync the moment a Job changes (or after a reconnect);
 * the 1s poll while work is active is the fallback when a notice is dropped.
 */
export function startImportJobSync(): () => void {
  if (!isBrowser()) return () => {};
  syncSubscribers += 1;
  if (syncSubscribers === 1) {
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("online", onConnectivityRestored);
    stopEvents = subscribeEvents((notice) => {
      if (notice.type === "job" || notice.type === "resync") wakeImportJobSync();
    });
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
      stopEvents?.();
      stopEvents = null;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("online", onConnectivityRestored);
    }
  };
}

export function subscribeTasks(callback: () => void): () => void {
  if (!isBrowser()) return () => {};
  window.addEventListener(TASK_EVENT, callback);
  return () => window.removeEventListener(TASK_EVENT, callback);
}

export function subscribeImportJobCompletions(
  callback: (job: JobStatus) => void | Promise<void>,
): () => void {
  if (!isBrowser()) return () => {};
  const listener = (event: CustomEvent<JobStatus>) => {
    void callback(event.detail);
  };
  window.addEventListener(TERMINAL_EVENT, listener);
  return () => window.removeEventListener(TERMINAL_EVENT, listener);
}

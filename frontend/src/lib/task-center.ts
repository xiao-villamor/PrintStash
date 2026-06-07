"use client";

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface TaskItem {
  id: string;
  title: string;
  detail?: string;
  status: TaskStatus;
  progress: number;
  createdAt: number;
  updatedAt: number;
}

const TASK_EVENT = "printstash:tasks-changed";
const COMPLETED_TTL_MS = 12_000;
const FAILED_TTL_MS = 30_000;
let tasks: TaskItem[] = [];
let cleanupTimer: ReturnType<typeof setTimeout> | null = null;

function emit() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(TASK_EVENT));
}

function clampProgress(progress: number): number {
  return Math.max(0, Math.min(100, Math.round(progress)));
}

function taskTtl(task: TaskItem): number | null {
  if (task.status === "completed") return COMPLETED_TTL_MS;
  if (task.status === "failed") return FAILED_TTL_MS;
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

export function createTask(input: {
  title: string;
  detail?: string;
  status?: TaskStatus;
  progress?: number;
}): string {
  const now = Date.now();
  const id = `${now}-${Math.random().toString(36).slice(2, 8)}`;
  tasks = [
    {
      id,
      title: input.title,
      detail: input.detail,
      status: input.status ?? "pending",
      progress: clampProgress(input.progress ?? 0),
      createdAt: now,
      updatedAt: now,
    },
    ...tasks,
  ].slice(0, 20);
  emit();
  scheduleCleanup();
  return id;
}

export function updateTask(
  id: string,
  patch: Partial<Pick<TaskItem, "title" | "detail" | "status" | "progress">>,
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
  emit();
  scheduleCleanup();
}

export function clearCompletedTasks(): void {
  tasks = tasks.filter(
    (task) => task.status !== "completed" && task.status !== "failed",
  );
  emit();
  scheduleCleanup();
}

export function subscribeTasks(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(TASK_EVENT, callback);
  return () => window.removeEventListener(TASK_EVENT, callback);
}

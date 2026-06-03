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
let tasks: TaskItem[] = [];

function emit() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(TASK_EVENT));
}

function clampProgress(progress: number): number {
  return Math.max(0, Math.min(100, Math.round(progress)));
}

export function listTasks(): TaskItem[] {
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
          updatedAt: now,
        }
      : task,
  );
  emit();
}

export function clearCompletedTasks(): void {
  tasks = tasks.filter(
    (task) => task.status !== "completed" && task.status !== "failed",
  );
  emit();
}

export function subscribeTasks(callback: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(TASK_EVENT, callback);
  return () => window.removeEventListener(TASK_EVENT, callback);
}

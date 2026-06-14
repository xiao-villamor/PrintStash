import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// task-center holds module-private state, so each test gets a fresh module via
// resetModules() + dynamic import. Fake timers (which also fake Date.now in
// vitest) drive the TTL-based pruning of completed/failed tasks.
type TaskCenter = typeof import("@/lib/task-center");

let tc: TaskCenter;

beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));
  tc = await import("@/lib/task-center");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createTask", () => {
  it("creates a pending task with a unique id and zero progress", () => {
    const id = tc.createTask({ title: "Upload Cube" });
    const tasks = tc.listTasks();
    expect(tasks).toHaveLength(1);
    expect(tasks[0]).toMatchObject({
      id,
      title: "Upload Cube",
      status: "pending",
      progress: 0,
    });
  });

  it("clamps progress into 0..100", () => {
    tc.createTask({ title: "over", progress: 150 });
    expect(tc.listTasks()[0].progress).toBe(100);
    vi.advanceTimersByTime(1);
    tc.createTask({ title: "under", progress: -10 });
    expect(tc.listTasks()[0].progress).toBe(0);
  });

  it("keeps at most 20 tasks", () => {
    for (let i = 0; i < 25; i++) {
      tc.createTask({ title: `t${i}` });
      vi.advanceTimersByTime(1);
    }
    expect(tc.listTasks()).toHaveLength(20);
  });
});

describe("updateTask", () => {
  it("patches fields and bumps updatedAt", () => {
    const id = tc.createTask({ title: "Send", status: "running", progress: 10 });
    vi.advanceTimersByTime(5);
    tc.updateTask(id, { detail: "2/3 done", progress: 66 });
    const task = tc.listTasks()[0];
    expect(task.detail).toBe("2/3 done");
    expect(task.progress).toBe(66);
  });

  it("forces progress to 100 when status becomes completed", () => {
    const id = tc.createTask({ title: "x", status: "running", progress: 40 });
    tc.updateTask(id, { status: "completed", progress: 50 });
    expect(tc.listTasks()[0].progress).toBe(100);
  });

  it("ignores updates to unknown ids", () => {
    tc.createTask({ title: "x" });
    tc.updateTask("does-not-exist", { progress: 99 });
    expect(tc.listTasks()[0].progress).toBe(0);
  });
});

describe("listTasks ordering", () => {
  it("returns most-recently-updated first", () => {
    tc.createTask({ title: "first" });
    vi.advanceTimersByTime(1000);
    tc.createTask({ title: "second" });
    expect(tc.listTasks().map((t) => t.title)).toEqual(["second", "first"]);
  });
});

describe("TTL pruning", () => {
  it("drops completed tasks after their TTL elapses", () => {
    const id = tc.createTask({ title: "done" });
    tc.updateTask(id, { status: "completed" });
    expect(tc.listTasks()).toHaveLength(1);

    vi.advanceTimersByTime(12_000 + 1);
    expect(tc.listTasks()).toHaveLength(0);
  });

  it("keeps running tasks indefinitely (no TTL)", () => {
    tc.createTask({ title: "long", status: "running" });
    vi.advanceTimersByTime(60_000);
    expect(tc.listTasks()).toHaveLength(1);
  });
});

describe("clearCompletedTasks", () => {
  it("removes completed/failed but keeps active tasks", () => {
    const a = tc.createTask({ title: "running", status: "running" });
    const b = tc.createTask({ title: "done", status: "running" });
    tc.updateTask(b, { status: "completed" });

    tc.clearCompletedTasks();
    const titles = tc.listTasks().map((t) => t.title);
    expect(titles).toEqual(["running"]);
    expect(tc.listTasks()[0].id).toBe(a);
  });
});

describe("subscribeTasks", () => {
  it("notifies subscribers on change and stops after unsubscribe", () => {
    const cb = vi.fn();
    const unsubscribe = tc.subscribeTasks(cb);

    tc.createTask({ title: "x" });
    expect(cb).toHaveBeenCalledTimes(1);

    unsubscribe();
    tc.createTask({ title: "y" });
    expect(cb).toHaveBeenCalledTimes(1);
  });
});

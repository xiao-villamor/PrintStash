/*
 * The task list a user watches while their uploads and imports run.
 *
 * Two kinds of work share one surface and they have opposite lifetimes. A
 * browser-local upload lives and dies with the tab; a server import outlives it,
 * and reconnecting to one after a reload must *not* cancel it — the user closed a
 * tab, not an import. That is the single most consequential row here.
 *
 * The retention rules follow from that. Running tasks never expire, completed
 * summaries stay until the user clears them, and clearing must not resurrect a
 * server job on the next sync. A cleared task that comes back is indistinguishable
 * from an import restarting itself.
 *
 * Grouping matters for the same reason: one upload of a mesh plus its G-code is
 * one task, because it is one thing the user did. Two entries make it look like
 * something was uploaded twice.
 *
 * The synchronizer is adaptive on purpose — it stops when the server is idle and
 * wakes on a new job or on connectivity returning. A fixed interval polls a quiet
 * backend forever from every open tab; one that never wakes leaves an import
 * looking stalled until a reload.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IngestJobSource } from "@/lib/task-center";
import type { IngestJobStatus } from "@/types";

// task-center holds module-private state, so each test gets a fresh module via
// resetModules() + dynamic import. Fake timers (which also fake Date.now in
// vitest) drive the TTL-based pruning of completed/failed tasks. The server's
// job list is injected through the module's own IngestJobSource seam, so no
// network (and no module mocking) is involved.
type TaskCenter = typeof import("@/lib/task-center");

const listIngestJobs = vi.fn<IngestJobSource>();

/** Fresh module instance, wired to the stubbed job source. */
async function loadTaskCenter(): Promise<TaskCenter> {
  const taskCenter = await import("@/lib/task-center");
  taskCenter.setIngestJobSource(listIngestJobs);
  return taskCenter;
}

let tc: TaskCenter;

beforeEach(async () => {
  vi.resetModules();
  listIngestJobs.mockReset();
  listIngestJobs.mockResolvedValue([]);
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));
  tc = await loadTaskCenter();
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

describe("listTasks", () => {
  it("returns most-recently-updated first", () => {
    tc.createTask({ title: "first" });
    vi.advanceTimersByTime(1000);
    tc.createTask({ title: "second" });
    expect(tc.listTasks().map((t) => t.title)).toEqual(["second", "first"]);
  });
});

describe("pruneTasks", () => {
  it("keeps completed summaries until the user clears them", () => {
    const id = tc.createTask({ title: "done" });
    tc.updateTask(id, { status: "completed" });
    expect(tc.listTasks()).toHaveLength(1);

    vi.advanceTimersByTime(12_000 + 1);
    expect(tc.listTasks()).toHaveLength(1);
  });

  it("keeps running tasks indefinitely (no TTL)", () => {
    tc.createTask({ title: "long", status: "running" });
    vi.advanceTimersByTime(60_000);
    expect(tc.listTasks()).toHaveLength(1);
  });
});

describe("trackServerJob", () => {
  it("persists a tracked server job across UI reloads without cancelling it", async () => {
    const id = tc.trackImportJob("server-job-1", "Import archive");
    expect(tc.listTasks()[0]).toMatchObject({ id, jobId: "server-job-1", status: "pending" });

    vi.resetModules();
    tc = await loadTaskCenter();
    expect(tc.listTasks()[0]).toMatchObject({ jobId: "server-job-1", status: "pending" });
  });

  it("does not let terminal history evict browser-local upload tasks", async () => {
    const localTitles = ["Upload part-0.stl", "Upload part-1.stl", "Upload part-2.stl"];
    localTitles.forEach((title) => tc.createTask({ title }));
    listIngestJobs.mockResolvedValue(
      Array.from({ length: 20 }, (_, index) => ({
        job_id: `historical-job-${index}`,
        state: "completed",
        model_id: index + 1,
        file_id: index + 1,
        error: null,
        started_at: null,
        finished_at: "2026-06-14T11:00:00Z",
      })),
    );

    await tc.syncImportJobs();

    expect(
      tc
        .listTasks()
        .map((task) => task.title)
        .sort(),
    ).toEqual(localTitles.sort());
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

  it("does not restore a cleared server job during the next sync", async () => {
    const id = tc.trackImportJob("server-job-1", "Import");
    tc.updateTask(id, { status: "completed" });
    tc.clearCompletedTasks();

    listIngestJobs.mockResolvedValue([
      {
        job_id: "server-job-1",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
      },
    ]);
    await tc.syncImportJobs();

    expect(tc.listTasks()).toEqual([]);

    vi.resetModules();
    tc = await loadTaskCenter();
    await tc.syncImportJobs();
    expect(tc.listTasks()).toEqual([]);
  });
});

describe("groupUploadJobs", () => {
  it("keeps mesh and G-code jobs from one upload in one task", async () => {
    const taskId = tc.createTask({
      title: "Upload Benchy",
      status: "running",
      expectedJobCount: 2,
    });
    tc.linkTaskToJob(taskId, "mesh-job");
    tc.linkTaskToJob(taskId, "gcode-job");
    listIngestJobs.mockResolvedValue([
      {
        job_id: "mesh-job",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 100,
      },
      {
        job_id: "gcode-job",
        state: "completed",
        model_id: 1,
        file_id: 2,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 100,
      },
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toHaveLength(1);
    expect(tc.listTasks()[0]).toMatchObject({
      id: taskId,
      status: "completed",
      progress: 100,
      jobIds: ["mesh-job", "gcode-job"],
    });
  });
});

describe("subscribeTasks", () => {
  it("notifies subscribers on change and stops after unsubscribe", () => {
    const cb = vi.fn<() => void>();
    const unsubscribe = tc.subscribeTasks(cb);

    tc.createTask({ title: "x" });
    expect(cb).toHaveBeenCalledTimes(1);

    unsubscribe();
    tc.createTask({ title: "y" });
    expect(cb).toHaveBeenCalledTimes(1);
  });
});

describe("syncImportJobs", () => {
  it("emits one completion event per job id and never regresses terminal state", async () => {
    const completed = vi.fn<(job: IngestJobStatus) => void>();
    const unsubscribe = tc.subscribeImportJobCompletions(completed);
    tc.trackImportJob("terminal-job", "Import archive");
    listIngestJobs.mockResolvedValue([
      {
        job_id: "terminal-job",
        state: "completed",
        model_id: 7,
        file_id: 9,
        error: null,
        started_at: null,
        finished_at: "2026-06-14T12:00:01Z",
        updated_at: "2026-06-14T12:00:01Z",
        completion: "partial",
        thumbnail_status: "failed",
        thumbnail_reason: "renderer_no_output",
      },
    ]);
    await tc.syncImportJobs();
    await tc.syncImportJobs();
    expect(completed).toHaveBeenCalledTimes(1);
    expect(tc.listTasks()[0]).toMatchObject({
      status: "completed",
      completion: "partial",
      thumbnailReason: "renderer_no_output",
    });

    listIngestJobs.mockResolvedValue([
      {
        job_id: "terminal-job",
        state: "running",
        model_id: 7,
        file_id: 9,
        error: null,
        started_at: null,
        finished_at: null,
        updated_at: "2026-06-14T11:59:59Z",
      },
    ]);
    await tc.syncImportJobs();
    expect(tc.listTasks()[0].status).toBe("completed");
    unsubscribe();
  });

  it("waits through Task Center instead of creating a competing poller", async () => {
    listIngestJobs.mockResolvedValue([
      {
        job_id: "awaited-job",
        state: "completed",
        model_id: 3,
        file_id: 4,
        error: null,
        started_at: null,
        finished_at: "2026-06-14T12:00:01Z",
      },
    ]);
    const status = await tc.waitForImportJob("awaited-job", "Await import");
    expect(status.state).toBe("completed");
    expect(listIngestJobs).toHaveBeenCalledTimes(1);
  });
});

describe("createImportJobSynchronizer", () => {
  it("stops polling after the initial sync when the server is idle", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listIngestJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(listIngestJobs).toHaveBeenCalledTimes(1);
    stop();
  });

  it("polls active jobs and cleanup releases the timer", async () => {
    listIngestJobs.mockResolvedValue([
      {
        job_id: "active-job",
        state: "running",
        model_id: null,
        file_id: null,
        error: null,
        started_at: null,
        finished_at: null,
      },
    ]);
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listIngestJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(listIngestJobs).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listIngestJobs).toHaveBeenCalledTimes(2);
  });

  it("wakes an idle synchronizer when a new server job is tracked", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listIngestJobs).toHaveBeenCalledTimes(1);

    tc.trackImportJob("new-job", "Scan library");
    await vi.advanceTimersByTimeAsync(0);
    expect(listIngestJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("backs off after a failed sync even without a local task record", async () => {
    listIngestJobs.mockRejectedValueOnce(new Error("offline")).mockResolvedValue([]);
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listIngestJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(999);
    expect(listIngestJobs).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(listIngestJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("wakes immediately when connectivity returns", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(listIngestJobs).toHaveBeenCalledTimes(2);
    stop();
  });
});

describe("waitForImportJob", () => {
  /** One job, trimmed to the fields the task centre reads. */
  function aJob(over: Partial<IngestJobStatus> = {}): IngestJobStatus {
    return {
      job_id: "job-1",
      state: "completed",
      model_id: 1,
      file_id: 1,
      error: null,
      started_at: null,
      finished_at: null,
      ...over,
    };
  }

  it("resolves as soon as the job is already terminal", async () => {
    // Every modal workflow awaits this rather than starting a second polling
    // loop of its own; making them wait a full poll interval for an answer the
    // module already has would stall each one by a second.
    listIngestJobs.mockResolvedValue([aJob()]);

    const job = await tc.waitForImportJob("job-1");

    expect(job.state).toBe("completed");
  });

  it("resolves with the failure rather than throwing", async () => {
    // The caller decides what a failure means; throwing here would make every
    // caller wrap the wait in a try just to read the reason.
    listIngestJobs.mockResolvedValue([aJob({ state: "failed", error: "unsupported_file_type" })]);

    const job = await tc.waitForImportJob("job-1");

    expect(job.error).toBe("unsupported_file_type");
  });

  it("waits for a job that is still running", async () => {
    listIngestJobs.mockResolvedValue([aJob({ state: "running" })]);
    const pending = tc.waitForImportJob("job-1");
    let settled = false;
    void pending.then(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(50);

    expect(settled).toBe(false);
  });

  it("resolves once a running job finishes", async () => {
    listIngestJobs.mockResolvedValue([aJob({ state: "running" })]);
    const pending = tc.waitForImportJob("job-1");
    await vi.advanceTimersByTimeAsync(50);

    listIngestJobs.mockResolvedValue([aJob({ state: "completed" })]);
    await vi.advanceTimersByTimeAsync(2_000);

    await expect(pending).resolves.toMatchObject({ state: "completed" });
  });

  it("gives up rather than waiting forever", async () => {
    // A job the server forgot about would otherwise hold a modal's spinner for
    // the life of the tab.
    listIngestJobs.mockResolvedValue([aJob({ state: "running" })]);
    const pending = tc.waitForImportJob("job-1", "Import", 5_000);
    // The rejection fires *inside* the timer advance, so something has to be
    // listening before it: with the first handler attached only afterwards, node
    // reports an unhandled rejection and vitest counts it as a run error even
    // though the test passes.
    void pending.catch(() => {});
    await vi.advanceTimersByTimeAsync(6_000);

    await expect(pending).rejects.toThrow(/Timed out/);
  });

  it("keeps polling after a failed sync", async () => {
    // Losing the network must not end the wait: the job is still running on the
    // server, and the answer arrives when the connection comes back.
    listIngestJobs.mockRejectedValueOnce(new Error("offline"));
    listIngestJobs.mockResolvedValue([aJob({ state: "completed" })]);

    const pending = tc.waitForImportJob("job-1");
    await vi.advanceTimersByTimeAsync(3_000);

    await expect(pending).resolves.toMatchObject({ state: "completed" });
  });
});

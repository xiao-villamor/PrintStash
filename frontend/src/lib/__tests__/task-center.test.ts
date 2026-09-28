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

import { aJob } from "@/test-support/factories";
import type { EventSocket } from "@/lib/events";
import type { JobSource } from "@/lib/task-center";
import type { JobStatus } from "@/types";

// task-center holds module-private state, so each test gets a fresh module via
// resetModules() + dynamic import. Fake timers (which also fake Date.now in
// vitest) drive the TTL-based pruning of completed/failed tasks. The server's
// job list is injected through the module's own JobSource seam, and the events
// socket through its factory seam, so no network (and no module mocking) is
// involved.
type TaskCenter = typeof import("@/lib/task-center");

const listJobs = vi.fn<JobSource>();

/** A socket the test drives: `deliver` is the server sending one frame. */
class FakeEventSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();

  deliver(frame: { type: string; job_id?: string; state?: string }): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

let socket: FakeEventSocket;

/** Fresh module instance, wired to the stubbed job source and events socket. */
async function loadTaskCenter(): Promise<TaskCenter> {
  const taskCenter = await import("@/lib/task-center");
  const events = await import("@/lib/events");
  socket = new FakeEventSocket();
  events.setEventSocketFactory(async () => socket);
  taskCenter.setJobSource(listJobs);
  return taskCenter;
}

let tc: TaskCenter;

beforeEach(async () => {
  vi.resetModules();
  listJobs.mockReset();
  listJobs.mockResolvedValue([]);
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));
  tc = await loadTaskCenter();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createTask", () => {
  it("marks a browser ZIP transfer interrupted after reload", async () => {
    tc.createTask({ title: "Prepare parts.zip", status: "running", archiveUploading: true });

    vi.resetModules();
    const reloaded = await loadTaskCenter();

    expect(reloaded.listTasks()[0]).toMatchObject({
      status: "failed",
      archiveUploading: false,
      detail: "ZIP upload interrupted. Select the file again.",
    });
  });

  it("keeps the ZIP review destination when the server job completes", async () => {
    const taskId = tc.createTask({
      title: "Prepare parts.zip",
      status: "running",
      archiveUploading: true,
      archiveCollection: "parts",
      archiveTags: ["functional"],
    });
    listJobs.mockResolvedValue([
      aJob({ job_id: "archive-job", kind: "ingestion.archive_inspect", state: "completed" }),
    ]);

    tc.attachTaskToImportJob(taskId, "archive-job");
    await tc.syncImportJobs();

    const task = tc.listTasks()[0];
    expect(task).toMatchObject({
      jobId: "archive-job",
      status: "completed",
      archiveCollection: "parts",
      archiveTags: ["functional"],
    });
    expect(tc.needsArchiveReview(task)).toBe(true);
  });

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

  it("keeps a prepared ZIP available after newer tasks fill the history", async () => {
    const reviewId = tc.createTask({
      title: "Prepare archive.zip",
      jobId: "archive-1",
      jobKind: "ingestion.archive_inspect",
      status: "completed",
    });
    for (let i = 0; i < 25; i++) tc.createTask({ title: `other-${i}` });

    expect(tc.listTasks().some((task) => task.id === reviewId)).toBe(true);
    tc.clearCompletedTasks();
    expect(tc.listTasks().some((task) => task.id === reviewId)).toBe(true);

    vi.resetModules();
    tc = await loadTaskCenter();
    expect(tc.listTasks().some((task) => task.id === reviewId)).toBe(true);
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
    listJobs.mockResolvedValue(
      Array.from({ length: 20 }, (_, index) =>
        aJob({
          job_id: `historical-job-${index}`,
          state: "completed",
          model_id: index + 1,
          file_id: index + 1,
          error: null,
          started_at: null,
          finished_at: "2026-06-14T11:00:00Z",
        }),
      ),
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

    listJobs.mockResolvedValue([
      aJob({
        job_id: "server-job-1",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
      }),
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
  it("reports grouped progress across unfinished jobs", async () => {
    const taskId = tc.createTask({ title: "Upload Benchy", expectedJobCount: 3 });
    tc.linkTaskToJob(taskId, "mesh-job");
    tc.linkTaskToJob(taskId, "gcode-job");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
      }),
      aJob({
        job_id: "gcode-job",
        state: "running",
        model_id: 1,
        file_id: null,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 40,
        current_item: "Benchy.gcode",
        processed: 2,
        total: 5,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toHaveLength(1);
    expect(tc.listTasks()[0]).toMatchObject({
      id: taskId,
      status: "running",
      progress: 47,
      currentItem: "Benchy.gcode",
      detail: "running 2/5 · Benchy.gcode · continues in background",
    });
  });

  it("keeps unknown grouped progress pending", async () => {
    const taskId = tc.createTask({ title: "Upload Benchy", expectedJobCount: 2 });
    tc.linkTaskToJob(taskId, "mesh-job");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "queued",
        model_id: null,
        file_id: null,
        error: null,
        started_at: null,
        finished_at: null,
        current_item: "Benchy.stl",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toHaveLength(1);
    expect(tc.listTasks()[0]).toMatchObject({
      id: taskId,
      status: "pending",
      progress: 0,
      currentItem: "Benchy.stl",
      detail: "pending · Benchy.stl · continues in background",
    });
  });

  it("reports a failed grouped upload", async () => {
    const taskId = tc.createTask({ title: "Upload Benchy", expectedJobCount: 2 });
    tc.linkTaskToJob(taskId, "mesh-job");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "failed",
        model_id: null,
        file_id: null,
        error: "The source file could not be read",
        started_at: null,
        finished_at: null,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toHaveLength(1);
    expect(tc.listTasks()[0]).toMatchObject({
      id: taskId,
      status: "failed",
      progress: 100,
      detail: "The source file could not be read",
    });
  });

  it("removes a duplicate pending row persisted by an older client", async () => {
    localStorage.setItem(
      "printstash:import-tasks:v1",
      JSON.stringify([
        {
          id: "duplicate",
          title: "Import",
          status: "pending",
          progress: 0,
          createdAt: Date.now(),
          updatedAt: Date.now(),
          jobId: "mesh-job",
        },
        {
          id: "upload",
          title: "Upload Benchy",
          status: "running",
          progress: 50,
          createdAt: Date.now(),
          updatedAt: Date.now(),
          jobIds: ["mesh-job"],
          expectedJobCount: 1,
        },
      ]),
    );
    vi.resetModules();
    tc = await loadTaskCenter();
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 100,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks().map((task) => task.title)).toEqual(["Upload Benchy"]);
  });

  it("reuses the upload task when waiting for one of its linked jobs", async () => {
    const taskId = tc.createTask({
      title: "Upload Benchy",
      status: "running",
      expectedJobCount: 1,
    });
    tc.linkTaskToJob(taskId, "mesh-job");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 100,
      }),
    ]);

    await tc.waitForImportJob("mesh-job");

    expect(tc.listTasks()).toHaveLength(1);
    expect(tc.listTasks()[0]).toMatchObject({
      id: taskId,
      title: "Upload Benchy",
      status: "completed",
    });
  });

  it("keeps mesh and G-code jobs from one upload in one task", async () => {
    const taskId = tc.createTask({
      title: "Upload Benchy",
      status: "running",
      expectedJobCount: 2,
    });
    tc.linkTaskToJob(taskId, "mesh-job");
    tc.linkTaskToJob(taskId, "gcode-job");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 100,
      }),
      aJob({
        job_id: "gcode-job",
        state: "completed",
        model_id: 1,
        file_id: 2,
        error: null,
        started_at: null,
        finished_at: null,
        progress: 100,
      }),
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
  it("shows live portable import counts from the job", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "portable-import",
        kind: "ingestion.library_import",
        state: "running",
        stage: "ingesting",
        processed: 23,
        total: 92,
        succeeded: 22,
        skipped: 1,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({
      jobKind: "ingestion.library_import",
      status: "running",
      processed: 23,
      total: 92,
      progress: 25,
      detail: "ingesting 23/92 · continues in background",
    });
  });

  it("passes active task ids to the reconnect source", async () => {
    const taskId = tc.trackImportJob("missed-job", "Recreate Model preview images");
    tc.updateTask(taskId, { status: "running" });

    await tc.syncImportJobs();

    expect(listJobs).toHaveBeenCalledWith(["missed-job"]);
  });

  it("fails a direct task the server no longer reports", async () => {
    const taskId = tc.trackImportJob("forgotten-job", "Recreate Model preview images");
    tc.updateTask(taskId, { status: "running", detail: "thumbnailing 0/2" });

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({
      status: "failed",
      progress: 100,
      retryable: true,
    });
    expect(tc.listTasks()[0].detail).toMatch(/no longer available/i);
  });

  it("rejects a waiter whose Job the server no longer reports", async () => {
    // A vanished Job has no status: the caller is told so, and nothing is
    // announced as completed on its behalf.
    const completions = vi.fn<(job: JobStatus) => void>();
    const unsubscribe = tc.subscribeImportJobCompletions(completions);
    listJobs.mockResolvedValue([]);

    const waiting = tc.waitForImportJob("vanished-job", "Backup");

    await expect(waiting).rejects.toBeInstanceOf(tc.JobStatusUnavailableError);
    await expect(waiting).rejects.toMatchObject({ jobId: "vanished-job" });
    expect(completions).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("titles a non-import Job by its label", async () => {
    listJobs.mockResolvedValue([
      aJob({ job_id: "scan-job", kind: "sources.scan", state: "running", label: "Scan NAS" }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks().map((task) => task.title)).toContain("Scan NAS");
  });

  it("fails a grouped upload when a linked server job disappears", async () => {
    const taskId = tc.createTask({
      title: "Upload Benchy",
      status: "running",
      expectedJobCount: 2,
    });
    tc.linkTaskToJob(taskId, "mesh-job");
    tc.linkTaskToJob(taskId, "gcode-job");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "mesh-job",
        state: "completed",
        model_id: 1,
        file_id: 1,
        error: null,
        started_at: null,
        finished_at: "2026-06-14T12:00:01Z",
        progress: 100,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({
      status: "failed",
      progress: 100,
      retryable: true,
    });
    expect(tc.listTasks()[0].detail).toMatch(/no longer available/i);
  });

  it("emits one completion event per job id and never regresses terminal state", async () => {
    const completed = vi.fn<(job: JobStatus) => void>();
    const unsubscribe = tc.subscribeImportJobCompletions(completed);
    tc.trackImportJob("terminal-job", "Import archive");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "terminal-job",
        state: "completed",
        model_id: 7,
        file_id: 9,
        error: null,
        started_at: null,
        finished_at: "2026-06-14T12:00:01Z",
        updated_at: "2026-06-14T12:00:01Z",
        completion: "partial",
        succeeded: 2,
        failed: 1,
      }),
    ]);
    await tc.syncImportJobs();
    await tc.syncImportJobs();
    expect(completed).toHaveBeenCalledTimes(1);
    expect(tc.listTasks()[0]).toMatchObject({
      status: "completed",
      completion: "partial",
      detail: "2 succeeded · 1 failed",
    });

    listJobs.mockResolvedValue([
      aJob({
        job_id: "terminal-job",
        state: "running",
        model_id: 7,
        file_id: 9,
        error: null,
        started_at: null,
        finished_at: null,
        updated_at: "2026-06-14T11:59:59Z",
      }),
    ]);
    await tc.syncImportJobs();
    expect(tc.listTasks()[0].status).toBe("completed");
    unsubscribe();
  });

  it("shows an interrupted Job as waiting to resume, not failed", async () => {
    // Its process stopped; the reconciler runs it again on its own.
    tc.trackImportJob("resuming-job", "Import");
    listJobs.mockResolvedValue([aJob({ job_id: "resuming-job", state: "interrupted" })]);

    await tc.syncImportJobs();

    const task = tc.listTasks()[0];
    expect(task).toMatchObject({ status: "pending", jobState: "interrupted" });
    expect(tc.taskDetail(task)).toBe("Interrupted · resumes automatically");
  });

  it("finishes a cancelled Job's task without a result", async () => {
    tc.trackImportJob("withdrawn-job", "Import");
    listJobs.mockResolvedValue([aJob({ job_id: "withdrawn-job", state: "cancelled" })]);

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({ status: "failed", detail: "Cancelled" });
  });

  it("titles a discovered non-import Job by its label", async () => {
    listJobs.mockResolvedValue([
      aJob({ job_id: "backup-job", kind: "backups.create", label: "Backup", state: "running" }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskTitle(tc.listTasks()[0])).toBe("Backup");
  });

  it("titles a discovered import Job as an import", async () => {
    listJobs.mockResolvedValue([
      aJob({ job_id: "url-job", kind: "ingestion.url", label: "ingestion.url", state: "running" }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskTitle(tc.listTasks()[0])).toBe("Import");
  });

  it("waits through Task Center instead of creating a competing poller", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "awaited-job",
        state: "completed",
        model_id: 3,
        file_id: 4,
        error: null,
        started_at: null,
        finished_at: "2026-06-14T12:00:01Z",
      }),
    ]);
    const status = await tc.waitForImportJob("awaited-job", "Await import");
    expect(status.state).toBe("completed");
    expect(listJobs).toHaveBeenCalledTimes(1);
  });
});

describe("createImportJobSynchronizer", () => {
  it("stops polling after the initial sync when the server is idle", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(listJobs).toHaveBeenCalledTimes(1);
    stop();
  });

  it("polls active jobs and cleanup releases the timer", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "active-job",
        state: "running",
        model_id: null,
        file_id: null,
        error: null,
        started_at: null,
        finished_at: null,
      }),
    ]);
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("wakes an idle synchronizer when a new server job is tracked", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(1);

    tc.trackImportJob("new-job", "Scan library");
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("backs off after a failed sync even without a local task record", async () => {
    listJobs.mockRejectedValueOnce(new Error("offline")).mockResolvedValue([]);
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(999);
    expect(listJobs).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("wakes immediately when connectivity returns", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("wakes an idle synchronizer when a Job changes on the server", async () => {
    // A worker finished the Job; without the notice an idle Task Center would
    // show the old state until something else woke it.
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(1);

    socket.deliver({ type: "job", job_id: "j1", state: "completed" });
    await vi.advanceTimersByTimeAsync(0);

    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("refetches after the events socket reconnects", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);

    socket.deliver({ type: "resync" });
    await vi.advanceTimersByTimeAsync(0);

    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("ignores notices about derivatives", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);

    socket.deliver({ type: "derivative", state: "ready" });
    await vi.advanceTimersByTimeAsync(0);

    expect(listJobs).toHaveBeenCalledTimes(1);
    stop();
  });

  it("closes the events socket when nothing shows the Task Center", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(0);

    stop();

    expect(socket.close).toHaveBeenCalled();
  });
});

describe("taskStatusOf", () => {
  it.each([
    { state: "queued" as const, status: "pending" },
    { state: "interrupted" as const, status: "pending" },
    { state: "running" as const, status: "running" },
    { state: "completed" as const, status: "completed" },
    { state: "failed" as const, status: "failed" },
    { state: "cancelled" as const, status: "failed" },
  ])("shows a $state Job as $status", ({ state, status }) => {
    expect(tc.taskStatusOf(state)).toBe(status);
  });
});

describe("waitForImportJob", () => {
  /** The one Job every wait here follows. */
  function jobOne(over: Partial<JobStatus> = {}): JobStatus {
    return aJob({ job_id: "job-1", started_at: null, finished_at: null, ...over });
  }

  it("resolves as soon as the job is already terminal", async () => {
    // Every modal workflow awaits this rather than starting a second polling
    // loop of its own; making them wait a full poll interval for an answer the
    // module already has would stall each one by a second.
    listJobs.mockResolvedValue([jobOne()]);

    const job = await tc.waitForImportJob("job-1");

    expect(job.state).toBe("completed");
  });

  it("resolves with the failure rather than throwing", async () => {
    // The caller decides what a failure means; throwing here would make every
    // caller wrap the wait in a try just to read the reason.
    listJobs.mockResolvedValue([jobOne({ state: "failed", error: "unsupported_file_type" })]);

    const job = await tc.waitForImportJob("job-1");

    expect(job.error).toBe("unsupported_file_type");
  });

  it("waits for a job that is still running", async () => {
    listJobs.mockResolvedValue([jobOne({ state: "running" })]);
    const pending = tc.waitForImportJob("job-1");
    let settled = false;
    void pending.then(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(50);

    expect(settled).toBe(false);
  });

  it("resolves once a running job finishes", async () => {
    listJobs.mockResolvedValue([jobOne({ state: "running" })]);
    const pending = tc.waitForImportJob("job-1");
    await vi.advanceTimersByTimeAsync(50);

    listJobs.mockResolvedValue([jobOne({ state: "completed" })]);
    await vi.advanceTimersByTimeAsync(2_000);

    await expect(pending).resolves.toMatchObject({ state: "completed" });
  });

  it("gives up rather than waiting forever", async () => {
    // A job the server forgot about would otherwise hold a modal's spinner for
    // the life of the tab.
    listJobs.mockResolvedValue([jobOne({ state: "running" })]);
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
    listJobs.mockRejectedValueOnce(new Error("offline"));
    listJobs.mockResolvedValue([jobOne({ state: "completed" })]);

    const pending = tc.waitForImportJob("job-1");
    await vi.advanceTimersByTimeAsync(3_000);

    await expect(pending).resolves.toMatchObject({ state: "completed" });
  });
});

describe("task localization", () => {
  it("updates stored task messages on language change without translating user names", async () => {
    const { uiMessage, setLocale } = await import("@/lib/locale");
    tc.createTask({
      title: uiMessage("Upload {value1}", { value1: "Files {value2}" }),
      detail: uiMessage("Queued"),
    });
    const task = tc.listTasks()[0];
    expect(tc.taskTitle(task)).toBe("Upload Files {value2}");
    setLocale("es");
    expect(tc.taskTitle(task)).toBe("Cargar Files {value2}");
    expect(tc.taskDetail(task)).toBe("En cola");
    // Reloading task storage keeps the descriptor, not just the original English snapshot.
    vi.resetModules();
    const reloaded = await loadTaskCenter();
    expect(reloaded.taskTitle(reloaded.listTasks()[0])).toBe("Cargar Files {value2}");
  });

  it("translates legacy controlled task titles", async () => {
    const { setLocale } = await import("@/lib/locale");
    tc.createTask({ title: "Queued" });

    setLocale("es");

    expect(tc.taskTitle(tc.listTasks()[0])).toBe("En cola");
    setLocale("en");
  });

  it("localizes failed task error details", async () => {
    const { setLocale } = await import("@/lib/locale");
    tc.createTask({ title: "Backup", status: "failed", error: "backup_blob_missing" });

    setLocale("es");

    expect(tc.taskDetail(tc.listTasks()[0])).toBe(
      "Falta un archivo necesario para la copia de seguridad. Comprueba el almacenamiento y vuelve a intentarlo.",
    );
    setLocale("en");
  });
});

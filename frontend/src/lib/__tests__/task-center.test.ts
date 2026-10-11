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

import { adminSession, memberSession } from "@/test-support/render";
import { aJob } from "@/test-support/factories";
import { uiMessage } from "@/lib/locale";
import { aSimilarityRun } from "@/test-support/similarity";
import type { EventSocket } from "@/lib/events";
import type { JobSource } from "@/lib/task-center";
import type { JobStatus } from "@/types";
import type { SimilarityRun } from "@/types/similarity";

// task-center holds module-private state, so each test gets a fresh module via
// resetModules() + dynamic import. Fake timers (which also fake Date.now in
// vitest) drive the TTL-based pruning of completed/failed tasks. The server's
// job list is injected through the module's own JobSource seam, and the events
// socket through its factory seam, so no network (and no module mocking) is
// involved.
type TaskCenter = typeof import("@/lib/task-center");

const listJobs = vi.fn<JobSource>();
const getSimilarityRun = vi.fn<(id: number) => Promise<SimilarityRun>>();

/** A socket the test drives: `deliver` is the server sending one frame. */
class FakeEventSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();

  deliver(frame: { type: string; job_id?: string; state?: string; task_visible?: boolean }): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

let socket: FakeEventSocket;
let stopTaskScope: () => void = () => {};

/** Fresh module instance, wired to the stubbed job source and events socket. */
async function loadTaskCenter(): Promise<TaskCenter> {
  stopTaskScope();
  const taskCenter = await import("@/lib/task-center");
  stopTaskScope = taskCenter.startTaskCenterSessionScope();
  const events = await import("@/lib/events");
  socket = new FakeEventSocket();
  events.setEventSocketFactory(async () => socket);
  taskCenter.setJobSource(listJobs);
  taskCenter.setSimilarityRunSource(getSimilarityRun);
  return taskCenter;
}

let tc: TaskCenter;

beforeEach(async () => {
  vi.resetModules();
  listJobs.mockReset();
  listJobs.mockResolvedValue([]);
  getSimilarityRun.mockReset();
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-06-14T12:00:00Z"));
  const auth = await import("@/lib/auth-store");
  const user = adminSession().user;
  if (!user) throw new Error("missing_admin_fixture");
  auth.storeLogin("", user, { silent: true });
  tc = await loadTaskCenter();
});

afterEach(() => {
  stopTaskScope();
  vi.useRealTimers();
});

describe("resetTasksForNewSetup", () => {
  it("forgets old installation tasks before syncing a new installation", async () => {
    tc.trackImportJob("old-job", "reconcile");

    tc.resetTasksForNewSetup();
    await tc.syncImportJobs();

    expect(tc.listTasks()).toHaveLength(0);
    expect(listJobs).toHaveBeenCalledWith([]);
    expect(localStorage.getItem("printstash:import-tasks:v1")).toBeNull();
  });

  it("forgets dismissed job ids from the old installation", async () => {
    const taskId = tc.trackImportJob("reused-job", "Old import");
    tc.updateTask(taskId, { status: "completed" });
    tc.clearCompletedTasks();
    tc.resetTasksForNewSetup();
    listJobs.mockResolvedValue([aJob({ job_id: "reused-job", state: "running" })]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toMatchObject([{ jobId: "reused-job", status: "running" }]);
  });

  it("ignores a previous installation job response received after reset", async () => {
    let deliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () => new Promise<JobStatus[]>((resolve) => (deliver = resolve)),
    );
    tc.trackImportJob("old-job", "reconcile");
    const syncing = tc.syncImportJobs();

    tc.resetTasksForNewSetup();
    deliver([aJob({ job_id: "old-job", state: "running" })]);
    await syncing;

    expect(tc.listTasks()).toHaveLength(0);
  });
});

describe("createTask", () => {
  it.each(["pending", "running"] as const)(
    "marks an orphaned %s browser task interrupted after reload",
    async (status) => {
      tc.createTask({
        title: "Upload holder/base.stl",
        status,
        detail: uiMessage("Queued"),
        expectedJobCount: 1,
        retryable: true,
      });

      vi.resetModules();
      tc = await loadTaskCenter();
      await tc.syncImportJobs();

      const task = tc.listTasks()[0];
      expect(task).toMatchObject({ status: "failed", progress: 100, retryable: false });
      expect(tc.taskDetail(task)).toBe("Task interrupted. Start it again.");
    },
  );

  it("allows clearing an orphaned browser upload after reload", async () => {
    tc.createTask({ title: "Upload palette/base.stl", detail: "Queued", expectedJobCount: 1 });

    vi.resetModules();
    tc = await loadTaskCenter();
    await tc.syncImportJobs();
    tc.clearCompletedTasks();

    expect(tc.listTasks()).toEqual([]);
    expect(JSON.parse(localStorage.getItem("printstash:import-tasks:v1")!)).toEqual([]);
  });

  it("retains an interrupted upload session for recovery after reload", async () => {
    tc.createTask({
      title: "Upload holder.stl",
      status: "running",
      uploadSessionId: "recoverable-upload",
    });

    vi.resetModules();
    tc = await loadTaskCenter();

    expect(tc.listTasks()[0]).toMatchObject({
      status: "failed",
      uploadSessionId: "recoverable-upload",
      uploadPaused: true,
      retryable: true,
    });
    expect(tc.taskDetail(tc.listTasks()[0])).toBe(
      "Upload interrupted. Select the same file to resume.",
    );
  });

  it("keeps a grouped server upload active after reload", async () => {
    const id = tc.createTask({
      title: "Upload holder.stl",
      status: "running",
      expectedJobCount: 1,
    });
    tc.linkTaskToJob(id, "durable-mesh-job");

    vi.resetModules();
    tc = await loadTaskCenter();
    listJobs.mockResolvedValue([aJob({ job_id: "durable-mesh-job", state: "running" })]);
    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({
      id,
      status: "running",
      jobIds: ["durable-mesh-job"],
    });
  });

  it.each(["completed", "failed"] as const)(
    "preserves a %s task summary after reload",
    async (status) => {
      const id = tc.createTask({
        title: "Upload finished.stl",
        status,
        detail: "Original summary",
      });

      vi.resetModules();
      tc = await loadTaskCenter();

      expect(tc.listTasks()[0]).toMatchObject({ id, status, detail: "Original summary" });
    },
  );

  it("keeps a live browser upload pending while syncing an idle server", async () => {
    const id = tc.createTask({ title: "Upload holder.stl", detail: "Queued", expectedJobCount: 1 });

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({ id, status: "pending", detail: "Queued" });
  });

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

describe("trackSimilarityRun", () => {
  it("shows a queued analysis in Tasks as soon as it starts", () => {
    const id = tc.trackSimilarityRun(aSimilarityRun());

    expect(tc.listTasks()).toMatchObject([
      { id, similarityRunId: 1, title: "Similar model analysis", status: "pending" },
    ]);
  });

  it("keeps one analysis task across reloads", async () => {
    const id = tc.trackSimilarityRun(aSimilarityRun());

    vi.resetModules();
    tc = await loadTaskCenter();
    expect(tc.trackSimilarityRun(aSimilarityRun())).toBe(id);
    expect(tc.listTasks()).toHaveLength(1);
  });

  it("shows running comparison counts from the durable run", async () => {
    tc.trackSimilarityRun(aSimilarityRun());
    getSimilarityRun.mockResolvedValue(
      aSimilarityRun({ state: "running", counters: { artifacts_processed: 3, verified: 2 } }),
    );

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({
      similarityRunId: 1,
      status: "running",
      detail: "3 Artifacts · 2 comparisons",
    });
  });

  it("completes the task only when the analysis run completes", async () => {
    tc.trackSimilarityRun(aSimilarityRun());
    getSimilarityRun.mockResolvedValue(aSimilarityRun({ state: "completed" }));

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({ status: "completed", progress: 100 });
  });

  it("reports a failed analysis in Tasks", async () => {
    tc.trackSimilarityRun(aSimilarityRun());
    getSimilarityRun.mockResolvedValue(aSimilarityRun({ state: "failed" }));

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({
      status: "failed",
      detail: "Analysis stopped. Check the run details and try again.",
    });
  });

  it("reports a cancelled analysis in Tasks", async () => {
    tc.trackSimilarityRun(aSimilarityRun());
    getSimilarityRun.mockResolvedValue(aSimilarityRun({ state: "cancelled" }));

    await tc.syncImportJobs();

    expect(tc.listTasks()[0]).toMatchObject({ status: "failed", detail: "Cancelled" });
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
    localStorage.setItem("printstash:task-owner:v1", "1");
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
  it("shows the number of ZIP models imported while the job runs", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "zip-import",
        kind: "ingestion.archive_selection",
        state: "running",
        stage: "ingesting",
        processed: 2,
        succeeded: 1,
        failed: 1,
        total: 4,
        progress: 50,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskDetail(tc.listTasks()[0])).toBe(
      "1 of 4 models imported · continues in background",
    );
  });

  it("uses the live ZIP job percentage for task progress", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "zip-import",
        kind: "ingestion.archive_selection",
        state: "running",
        stage: "ingesting",
        processed: 2,
        total: 4,
        progress: 50,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()[0].progress).toBe(50);
  });

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

  it("describes manual backup archive progress from its Job", async () => {
    tc.trackImportJob("backup-progress", "Backup");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "backup-progress",
        kind: "backups.create",
        state: "running",
        stage: "archiving",
        processed: 3,
        total: 8,
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskDetail(tc.listTasks()[0])).toBe(
      "Archiving 3 of 8 files · continues in background",
    );
  });

  it("describes the scheduled backup publication destination", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "scheduled-backup",
        kind: "backups.automatic",
        state: "running",
        stage: "publishing",
        current_item: "Offsite",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskDetail(tc.listTasks()[0])).toBe(
      "Publishing backup to Offsite · continues in background",
    );
  });

  it("shows a scheduled backup that finished before the browser discovered it", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "fast-scheduled-backup",
        kind: "backups.automatic",
        state: "completed",
        completion: "complete",
        updated_at: "2026-06-14T11:59:00Z",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toMatchObject([
      {
        jobId: "fast-scheduled-backup",
        status: "completed",
        detail: "Backup created",
      },
    ]);
  });

  it("does not restore an older scheduled backup after clearing the latest", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "older-scheduled-backup",
        kind: "backups.automatic",
        state: "completed",
        updated_at: "2026-06-14T10:00:00Z",
      }),
      aJob({
        job_id: "latest-scheduled-backup",
        kind: "backups.automatic",
        state: "completed",
        updated_at: "2026-06-14T11:00:00Z",
      }),
    ]);

    await tc.syncImportJobs();
    expect(tc.listTasks().map((task) => task.jobId)).toEqual(["latest-scheduled-backup"]);

    tc.clearCompletedTasks();
    await tc.syncImportJobs();

    expect(tc.listTasks()).toEqual([]);
  });

  it("ignores stale scheduled backup history", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "stale-scheduled-backup",
        kind: "backups.automatic",
        state: "completed",
        updated_at: "2026-06-12T11:00:00Z",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toEqual([]);
  });

  it("reports a partial backup instead of a generic Job count", async () => {
    tc.trackImportJob("partial-backup", "Backup");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "partial-backup",
        kind: "backups.create",
        state: "completed",
        completion: "partial",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskDetail(tc.listTasks()[0])).toBe("Backup created; some destinations failed");
  });

  it("titles a discovered scratch cleanup Job with its supplied label", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "scratch-cleanup-job",
        kind: "ingestion.scratch_cleanup",
        label: "Ingestion temporary files",
        state: "running",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskTitle(tc.listTasks()[0])).toBe("Ingestion temporary files");
  });

  it("titles a discovered archive inspection Job as ZIP preparation", async () => {
    listJobs.mockResolvedValue([
      aJob({
        job_id: "archive-inspect-job",
        kind: "ingestion.archive_inspect",
        label: "ingestion.archive_inspect",
        state: "running",
      }),
    ]);

    await tc.syncImportJobs();

    expect(tc.taskTitle(tc.listTasks()[0])).toBe("Prepare ZIP");
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

describe("attachTaskToImportJob", () => {
  it("preserves a discovered Job after local task removal", async () => {
    const localId = tc.createTask({ title: "Prepare parts.zip", status: "completed" });
    listJobs.mockResolvedValue([
      aJob({ job_id: "zip-job", kind: "ingestion.archive_inspect", state: "running" }),
    ]);
    await tc.syncImportJobs();
    tc.clearCompletedTasks();
    const discovered = tc.listTasks();

    tc.attachTaskToImportJob(localId, "zip-job");

    expect(tc.listTasks()).toEqual(discovered);
    expect(tc.listTasks()).toMatchObject([{ jobId: "zip-job", status: "running" }]);
  });

  it("attaches a ZIP receipt after Job discovery", async () => {
    const taskId = tc.createTask({
      title: "Prepare parts.zip",
      archiveSizeBytes: 100,
      archiveCollection: "Parts",
      archiveTags: ["tag"],
      archiveUploading: true,
    });
    listJobs.mockResolvedValue([
      aJob({ job_id: "zip-job", kind: "ingestion.archive_inspect", state: "running" }),
    ]);
    await tc.syncImportJobs();

    tc.attachTaskToImportJob(taskId, "zip-job");
    listJobs.mockResolvedValue([
      aJob({ job_id: "zip-job", kind: "ingestion.archive_inspect", state: "completed" }),
    ]);
    await tc.syncImportJobs();

    expect(tc.listTasks()).toMatchObject([
      {
        id: taskId,
        title: "Prepare parts.zip",
        status: "completed",
        archiveCollection: "Parts",
        archiveTags: ["tag"],
      },
    ]);
    expect(tc.needsArchiveReview(tc.listTasks()[0])).toBe(true);
  });

  it("attaches a ZIP receipt after terminal discovery", async () => {
    const taskId = tc.createTask({
      title: "Prepare parts.zip",
      archiveSizeBytes: 100,
      archiveUploading: true,
    });
    listJobs.mockResolvedValue([
      aJob({ job_id: "zip-job", kind: "ingestion.archive_inspect", state: "running" }),
    ]);
    await tc.syncImportJobs();
    listJobs.mockResolvedValue([
      aJob({ job_id: "zip-job", kind: "ingestion.archive_inspect", state: "completed" }),
    ]);
    await tc.syncImportJobs();

    tc.attachTaskToImportJob(taskId, "zip-job");

    expect(tc.listTasks()).toMatchObject([
      { id: taskId, title: "Prepare parts.zip", status: "completed" },
    ]);
    expect(tc.needsArchiveReview(tc.listTasks()[0])).toBe(true);
  });

  it("repairs a persisted duplicate ZIP task", async () => {
    tc.createTask({
      title: "Prepare ZIP",
      jobId: "zip-job",
      status: "completed",
      jobKind: "ingestion.archive_inspect",
    });
    const taskId = tc.createTask({
      title: "Prepare parts.zip",
      jobId: "zip-job",
      archiveSizeBytes: 100,
      archiveCollection: "Parts",
      archiveTags: ["tag"],
      status: "pending",
    });
    vi.resetModules();
    tc = await loadTaskCenter();
    listJobs.mockResolvedValue([
      aJob({ job_id: "zip-job", kind: "ingestion.archive_inspect", state: "completed" }),
    ]);

    await tc.syncImportJobs();

    expect(tc.listTasks()).toMatchObject([
      {
        id: taskId,
        title: "Prepare parts.zip",
        status: "completed",
        archiveCollection: "Parts",
        archiveTags: ["tag"],
      },
    ]);
    expect(tc.needsArchiveReview(tc.listTasks()[0])).toBe(true);
  });
});

describe("createImportJobSynchronizer", () => {
  async function handshake() {
    await vi.advanceTimersByTimeAsync(0);
    socket.deliver({ type: "resync" });
    await vi.advanceTimersByTimeAsync(0);
  }

  it("resumes Job synchronization when the tab becomes visible", async () => {
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    listJobs.mockResolvedValue([aJob({ job_id: "visible-job", state: "running" })]);
    const stop = tc.startImportJobSync();
    try {
      await handshake();
      visibility.mockReturnValue("hidden");
      document.dispatchEvent(new Event("visibilitychange"));
      listJobs.mockResolvedValue([aJob({ job_id: "visible-job", state: "completed" })]);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(listJobs).toHaveBeenCalledTimes(1);
      expect(tc.listTasks()).toMatchObject([{ jobId: "visible-job", status: "running" }]);

      visibility.mockReturnValue("visible");
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);

      expect(listJobs).toHaveBeenCalledTimes(2);
      expect(tc.listTasks()).toMatchObject([{ jobId: "visible-job", status: "completed" }]);
    } finally {
      stop();
      visibility.mockRestore();
    }
  });

  it("shares one handshake snapshot between subscribers", async () => {
    const stopA = tc.startImportJobSync();
    const stopB = tc.startImportJobSync();
    await handshake();
    await vi.advanceTimersByTimeAsync(1_500);
    expect(listJobs).toHaveBeenCalledTimes(1);
    stopA();
    stopB();
  });

  it("falls back after one second without a socket notice", async () => {
    const stop = tc.startImportJobSync();
    await vi.advanceTimersByTimeAsync(999);
    expect(listJobs).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(listJobs).toHaveBeenCalledTimes(1);
    stop();
  });

  it("retains a Job notice received during a snapshot", async () => {
    let deliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () =>
        new Promise<JobStatus[]>((resolve) => {
          deliver = resolve;
        }),
    );
    const stop = tc.startImportJobSync();
    await handshake();
    socket.deliver({ type: "job", job_id: "changed-job", state: "completed" });
    listJobs.mockResolvedValue([aJob({ job_id: "changed-job", state: "completed" })]);
    deliver([aJob({ job_id: "changed-job", state: "running" })]);
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(2);
    expect(tc.listTasks()).toMatchObject([{ jobId: "changed-job", status: "completed" }]);
    stop();
  });

  it("stops polling after the initial sync when the server is idle", async () => {
    const stop = tc.startImportJobSync();
    await handshake();
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
    await handshake();
    expect(listJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("wakes an idle synchronizer when a new server job is tracked", async () => {
    const stop = tc.startImportJobSync();
    await handshake();
    expect(listJobs).toHaveBeenCalledTimes(1);

    tc.trackImportJob("new-job", "Scan library");
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("backs off after a failed sync even without a local task record", async () => {
    listJobs.mockRejectedValueOnce(new Error("offline")).mockResolvedValue([]);
    const stop = tc.startImportJobSync();
    await handshake();
    expect(listJobs).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(999);
    expect(listJobs).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("wakes immediately when connectivity returns", async () => {
    const stop = tc.startImportJobSync();
    await handshake();
    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("wakes an idle synchronizer when a Job changes on the server", async () => {
    // A worker finished the Job; without the notice an idle Task Center would
    // show the old state until something else woke it.
    const stop = tc.startImportJobSync();
    await handshake();
    expect(listJobs).toHaveBeenCalledTimes(1);

    socket.deliver({ type: "job", job_id: "j1", state: "completed" });
    await vi.advanceTimersByTimeAsync(0);

    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("ignores maintenance outside the Task Center", async () => {
    listJobs.mockResolvedValue([aJob({ state: "completed" })]);
    const stop = tc.startImportJobSync();
    await handshake();
    for (let i = 0; i < 60; i++) {
      socket.deliver({
        type: "job",
        job_id: `maintenance-${i}`,
        state: "completed",
        task_visible: false,
      });
      await vi.advanceTimersByTimeAsync(500);
    }
    expect(listJobs).toHaveBeenCalledTimes(1);
    stop();
  });

  it("refreshes tasks for visible Job notices", async () => {
    const stop = tc.startImportJobSync();
    await handshake();
    socket.deliver({ type: "job", job_id: "owned-job", state: "completed", task_visible: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(listJobs).toHaveBeenCalledTimes(2);
    stop();
  });

  it("refetches after the events socket reconnects", async () => {
    listJobs.mockResolvedValue([aJob({ job_id: "reconnected-job", state: "running" })]);
    const stop = tc.startImportJobSync();
    await handshake();
    expect(tc.listTasks()).toMatchObject([{ jobId: "reconnected-job", status: "running" }]);

    listJobs.mockResolvedValue([aJob({ job_id: "reconnected-job", state: "completed" })]);
    socket.deliver({ type: "resync" });
    await vi.advanceTimersByTimeAsync(0);

    expect(listJobs).toHaveBeenCalledTimes(2);
    expect(tc.listTasks()).toMatchObject([{ jobId: "reconnected-job", status: "completed" }]);
    stop();
  });

  it("ignores notices about derivatives", async () => {
    const stop = tc.startImportJobSync();
    await handshake();

    socket.deliver({ type: "derivative", state: "ready" });
    await vi.advanceTimersByTimeAsync(0);

    expect(listJobs).toHaveBeenCalledTimes(1);
    stop();
  });

  it("closes the events socket when nothing shows the Task Center", async () => {
    const stop = tc.startImportJobSync();
    await handshake();

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

describe("policy cancellation", () => {
  it("settles neutrally without failure notifications", async () => {
    const completion = vi.fn<(job: JobStatus) => void>();
    const stop = tc.subscribeImportJobCompletions(completion);
    tc.trackImportJob("disabled-1", "Mesh derivatives");
    listJobs.mockResolvedValue([
      aJob({
        job_id: "disabled-1",
        kind: "derivatives.mesh",
        state: "cancelled",
        error: "derivative_group_disabled",
        retryable: false,
      }),
    ]);
    await tc.syncImportJobs();
    expect(tc.listTasks()[0]).toMatchObject({
      status: "completed",
      jobReason: "derivative_group_disabled",
      detail: "Processing disabled",
      retryable: false,
    });
    expect(completion).not.toHaveBeenCalled();
    stop();
  });
});

describe("private task scope", () => {
  it("retains recovery for the same verified owner", async () => {
    tc.createTask({ title: "Private upload", uploadSessionId: "recovery", status: "running" });
    vi.resetModules();
    tc = await loadTaskCenter();
    expect(tc.listTasks()).toMatchObject([
      { title: "Private upload", uploadSessionId: "recovery" },
    ]);
  });

  it.each(["legacy", "mismatched"])("discards %s persisted tasks", async (owner) => {
    tc.createTask({ title: "Old private label", uploadSessionId: "private-recovery" });
    if (owner === "legacy") localStorage.removeItem("printstash:task-owner:v1");
    else localStorage.setItem("printstash:task-owner:v1", "2");
    vi.resetModules();
    tc = await loadTaskCenter();
    expect(tc.listTasks()).toEqual([]);
    expect(localStorage.getItem("printstash:import-tasks:v1")).toBeNull();
  });

  it.each(["identity", "access", "logout"])(
    "retires private history on %s change",
    async (change) => {
      const auth = await import("@/lib/auth-store");
      tc.trackImportJob("dismissed", "Private label");
      listJobs.mockResolvedValue([aJob({ job_id: "dismissed", state: "completed" })]);
      await tc.syncImportJobs();
      tc.clearCompletedTasks();
      tc.createTask({ title: "Private upload", uploadSessionId: "private-recovery" });
      if (change === "access") auth.retirePrivateSessionScope();
      else if (change === "logout") auth.clearLogin();
      else {
        const user = memberSession().user;
        if (!user) throw new Error("missing_member_fixture");
        auth.storeLogin("", user);
      }
      expect(tc.listTasks()).toEqual([]);
      for (const key of [
        "printstash:import-tasks:v1",
        "printstash:dismissed-import-jobs:v1",
        "printstash:emitted-import-terminals:v1",
      ])
        expect(localStorage.getItem(key)).toBeNull();
      if (change === "logout") {
        const user = adminSession().user;
        if (!user) throw new Error("missing_admin_fixture");
        auth.storeLogin("", user);
      }
      listJobs.mockResolvedValue([aJob({ job_id: "dismissed", state: "running" })]);
      await tc.syncImportJobs();
      expect(tc.listTasks()).toMatchObject([{ jobId: "dismissed", status: "running" }]);
      const waiting = tc.waitForImportJob("dismissed");
      const rejected = waiting.catch((error: Error) => error);
      auth.retirePrivateSessionScope();
      await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
    },
  );

  it("rejects a retired completion waiter", async () => {
    listJobs.mockImplementation(() => new Promise(() => {}));
    const waiting = tc.waitForImportJob("pending");
    const rejected = waiting.catch((error: Error) => error);
    (await import("@/lib/auth-store")).retirePrivateSessionScope();
    await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
    expect(tc.listTasks()).toEqual([]);
  });

  it("rejects late source publication after retirement", async () => {
    let deliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    );
    tc.trackImportJob("old", "Old private label");
    const completed = vi.fn<(job: JobStatus) => void>();
    const stop = tc.subscribeImportJobCompletions(completed);
    const syncing = tc.syncImportJobs();
    const rejected = syncing.catch((error: Error) => error);
    (await import("@/lib/auth-store")).retirePrivateSessionScope();
    deliver([aJob({ job_id: "old", state: "completed" })]);
    await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
    expect(tc.listTasks()).toEqual([]);
    expect(completed).not.toHaveBeenCalled();
    stop();
  });

  it("coalesces concurrent snapshots within a scope", async () => {
    let deliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    );
    const first = tc.syncImportJobs();
    const second = tc.syncImportJobs();
    expect(first).toBe(second);
    expect(listJobs).toHaveBeenCalledTimes(1);
    deliver([]);
    await Promise.all([first, second]);
  });

  it("keeps a newer flight after an old finalizer", async () => {
    let oldDeliver: (jobs: JobStatus[]) => void = () => {};
    let newDeliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          oldDeliver = resolve;
        }),
    );
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          newDeliver = resolve;
        }),
    );
    const first = tc.syncImportJobs();
    const rejected = first.catch((error: Error) => error);
    (await import("@/lib/auth-store")).retirePrivateSessionScope();
    const newer = tc.syncImportJobs();
    oldDeliver([]);
    await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
    expect(tc.syncImportJobs()).toBe(newer);
    expect(listJobs).toHaveBeenCalledTimes(2);
    newDeliver([]);
    await newer;
  });

  it("fences reentrant terminal subscribers", async () => {
    const auth = await import("@/lib/auth-store");
    tc.trackImportJob("terminal", "Private label");
    const stopFirst = tc.subscribeImportJobCompletions(() => auth.retirePrivateSessionScope());
    const second = vi.fn<(job: JobStatus) => void>();
    const stopSecond = tc.subscribeImportJobCompletions(second);
    listJobs.mockResolvedValue([aJob({ job_id: "terminal", state: "completed" })]);
    await expect(tc.syncImportJobs()).rejects.toMatchObject({ name: "AbortError" });
    expect(second).not.toHaveBeenCalled();
    expect(tc.listTasks()).toEqual([]);
    stopFirst();
    stopSecond();
  });

  it("freezes both source readers for each snapshot", async () => {
    tc.trackSimilarityRun(aSimilarityRun({ id: 42, state: "running" }));
    const original = vi
      .fn<(id: number) => Promise<SimilarityRun>>()
      .mockResolvedValue(aSimilarityRun({ id: 42, state: "completed" }));
    const replacement = vi
      .fn<(id: number) => Promise<SimilarityRun>>()
      .mockResolvedValue(aSimilarityRun({ id: 42, state: "failed" }));
    tc.setSimilarityRunSource(original);
    listJobs.mockImplementationOnce(async () => {
      tc.setSimilarityRunSource(replacement);
      tc.setJobSource(async () => [aJob({ job_id: "replacement", state: "running" })]);
      return [];
    });
    await tc.syncImportJobs();
    expect(original).toHaveBeenCalledWith(42);
    expect(replacement).not.toHaveBeenCalled();
    expect(tc.listTasks()).toMatchObject([{ similarityRunId: 42, status: "completed" }]);
  });

  it("fences cached terminal delivery across retirement", async () => {
    tc.trackImportJob("cached", "Private label");
    listJobs.mockResolvedValue([aJob({ job_id: "cached", state: "completed" })]);
    await tc.syncImportJobs();
    const auth = await import("@/lib/auth-store");
    const waiting = tc.waitForImportJob("cached");
    const rejected = waiting.catch((error: Error) => error);
    auth.retirePrivateSessionScope();
    await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
  });

  it("shares a snapshot between completion waiters", async () => {
    let deliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    );
    const first = tc.waitForImportJob("shared");
    const second = tc.waitForImportJob("shared");
    expect(listJobs).toHaveBeenCalledTimes(1);
    deliver([aJob({ job_id: "shared", state: "completed" })]);
    await expect(first).resolves.toMatchObject({ job_id: "shared" });
    await expect(second).resolves.toMatchObject({ job_id: "shared" });
  });

  it("rejects late Similarity Run publication", async () => {
    let deliver: (run: SimilarityRun) => void = () => {};
    tc.trackSimilarityRun(aSimilarityRun({ id: 43, state: "running" }));
    getSimilarityRun.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    );
    const syncing = tc.syncImportJobs();
    const rejected = syncing.catch((error: Error) => error);
    (await import("@/lib/auth-store")).retirePrivateSessionScope();
    deliver(aSimilarityRun({ id: 43, state: "completed" }));
    await expect(rejected).resolves.toMatchObject({ name: "AbortError" });
    expect(tc.listTasks()).toEqual([]);
  });

  it("suppresses anonymous snapshot admission after logout", async () => {
    const stop = tc.startImportJobSync();
    try {
      await vi.advanceTimersByTimeAsync(1_000);
      expect(listJobs).toHaveBeenCalledTimes(1);
      (await import("@/lib/auth-store")).clearLogin();
      listJobs.mockResolvedValue([
        aJob({ job_id: "old-private", label: "Secret label", state: "running" }),
      ]);
      window.dispatchEvent(new Event("online"));
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(5_000);
      await tc.syncImportJobs();
      expect(listJobs).toHaveBeenCalledTimes(1);
      expect(tc.listTasks()).toEqual([]);
    } finally {
      stop();
    }
  });

  it("preserves a newer poll across old retirement finalization", async () => {
    let oldDeliver: (jobs: JobStatus[]) => void = () => {};
    let newDeliver: (jobs: JobStatus[]) => void = () => {};
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          oldDeliver = resolve;
        }),
    );
    listJobs.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          newDeliver = resolve;
        }),
    );
    const stop = tc.startImportJobSync();
    try {
      await vi.advanceTimersByTimeAsync(1_000);
      (await import("@/lib/auth-store")).retirePrivateSessionScope();
      await vi.advanceTimersByTimeAsync(0);
      expect(listJobs).toHaveBeenCalledTimes(2);
      oldDeliver([aJob({ job_id: "old-private", state: "running" })]);
      await vi.advanceTimersByTimeAsync(0);
      socket.deliver({ type: "resync" });
      await vi.advanceTimersByTimeAsync(0);
      expect(listJobs).toHaveBeenCalledTimes(2);
      newDeliver([]);
      await vi.advanceTimersByTimeAsync(0);
      expect(listJobs).toHaveBeenCalledTimes(3);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(listJobs).toHaveBeenCalledTimes(3);
      expect(tc.listTasks()).toEqual([]);
    } finally {
      stop();
    }
  });

  it("retirement survives unavailable browser storage", async () => {
    tc.createTask({ title: "Private label" });
    const remove = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    try {
      (await import("@/lib/auth-store")).retirePrivateSessionScope();
      expect(tc.listTasks()).toEqual([]);
      expect(localStorage.getItem("printstash:task-owner:v1")).toBe("null");
    } finally {
      remove.mockRestore();
    }
    vi.resetModules();
    tc = await loadTaskCenter();
    expect(tc.listTasks()).toEqual([]);
  });
});

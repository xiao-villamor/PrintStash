/** Exact backup retry gestures reconcile one bounded history owner without retiring durable work. */
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  backupRunKeys,
  backupRunsOptions,
  useBackupDestinationRetry,
} from "@/lib/queries/settings-backup-runs";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
import * as taskCenter from "@/lib/task-center";
import type { BackupRun } from "@/lib/api/backup";
import { aJob } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

const failedRun: BackupRun = {
  id: "run-owner",
  backup_id: "archive-owner",
  outcome: "partial",
  created_at: "2026-01-01T00:00:00Z",
  archive_sha256: "a".repeat(64),
  destinations: [
    {
      id: "destination-owner",
      run_id: "run-owner",
      name: "Failed replica",
      kind: "connection",
      outcome: "failed",
      error_code: "backup_remote_publication_failed",
      published_at: null,
      verified_at: null,
    },
  ],
};
function Editor({ session: stamped }: { session?: number }) {
  const history = useQuery(backupRunsOptions());
  const command = useBackupDestinationRetry();
  function retry() {
    void command
      .retry({
        session: stamped ?? getSessionVersion(),
        destinationId: "destination-owner",
        taskTitle: "Backup retry",
      })
      .catch(() => {});
  }
  return (
    <>
      <p>{history.data?.map((run) => run.outcome).join(",")}</p>
      <p>{command.error ? "Failed" : command.retrying ? "Pending" : "Ready"}</p>
      <button onClick={retry}>Retry</button>
    </>
  );
}
const reads = { "GET /api/v1/backups/runs": json([failedRun]) };
function retryRoutes(jobId: string) {
  return {
    ...reads,
    "POST /api/v1/backups/runs/destinations/destination-owner/retry": json(
      { job_id: jobId, state: "queued", message: "queued" },
      202,
    ),
    "GET /api/v1/jobs": json([
      aJob({ job_id: jobId, kind: "backups.retry_destination", model_id: null, file_id: null }),
    ]),
  };
}
afterEach(() => {
  vi.restoreAllMocks();
  taskCenter.resetTasksForNewSetup();
});
describe("Backup execution owner", () => {
  it("shares one canonical execution history", async () => {
    const app = renderApp(
      <>
        <Editor />
        <Editor />
      </>,
      { routes: reads },
    );
    await screen.findAllByText("partial");
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
    expect(app.client.getQueryCache().findAll({ queryKey: backupRunKeys.all })).toHaveLength(1);
  });
  it("refuses a retired retry gesture", async () => {
    const session = getSessionVersion();
    clearLogin();
    const app = renderApp(<Editor session={session} />, { routes: reads });
    await screen.findByText("partial");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(taskCenter.listTasks()).toHaveLength(0);
  });
  it.each(["missing", "published", "running", "unverified"] as const)(
    "revalidates a %s destination before dispatch",
    async (mode) => {
      const app = renderApp(<Editor />, { routes: reads });
      await screen.findByText("partial");
      const release = Promise.withResolvers<void>();
      const native = app.client.cancelQueries.bind(app.client);
      vi.spyOn(app.client, "cancelQueries").mockImplementationOnce(async (...args) => {
        await native(...args);
        await release.promise;
      });
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      await screen.findByText("Pending");
      const changed: BackupRun =
        mode === "published"
          ? {
              ...failedRun,
              destinations: failedRun.destinations.map((row) => ({ ...row, outcome: "completed" })),
            }
          : mode === "running"
            ? { ...failedRun, outcome: "running" }
            : { ...failedRun, archive_sha256: null };
      await act(async () => {
        app.client.setQueryData(backupRunKeys.all, mode === "missing" ? [] : [changed]);
        release.resolve();
      });
      await screen.findByText("Failed");
      expect(app.requestsWithMethod("POST")).toHaveLength(0);
      expect(taskCenter.listTasks()).toHaveLength(0);
    },
  );
  it("leaves a new same-key read active after retired terminal continuation", async () => {
    const terminal = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const native = taskCenter.waitForImportJob;
    vi.spyOn(taskCenter, "waitForImportJob").mockImplementation(async (...args) => {
      const job = await native(...args);
      terminal.resolve();
      await release.promise;
      return job;
    });
    const app = renderApp(<Editor />, { routes: retryRoutes("retired-terminal-owner") });
    await screen.findByText("partial");
    const cancel = vi.spyOn(app.client, "cancelQueries");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await terminal.promise;
    await act(async () => clearLogin());
    app.unmount();
    const fresh = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const current = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/backups/runs": (_url, init) => {
          signal = init?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => release.resolve());
    expect(signal?.aborted).toBe(false);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(current.requestsWithMethod("GET")).toHaveLength(1);
    await act(async () => fresh.resolve(json([{ ...failedRun, outcome: "completed" }])));
    expect(await screen.findByText("completed")).toBeVisible();
  });
  it("does not invalidate a new history during retired terminal cancellation", async () => {
    const app = renderApp(<Editor />, { routes: retryRoutes("cancel-retirement-owner") });
    await screen.findByText("partial");
    const reached = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const native = app.client.cancelQueries.bind(app.client);
    let calls = 0;
    vi.spyOn(app.client, "cancelQueries").mockImplementation(async (...args) => {
      await native(...args);
      calls += 1;
      if (calls === 2) {
        reached.resolve();
        await release.promise;
      }
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await reached.promise;
    await act(async () => clearLogin());
    app.unmount();
    const fresh = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const current = renderApp(<Editor />, {
      routes: {
        "GET /api/v1/backups/runs": (_url, init) => {
          signal = init?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => release.resolve());
    expect(signal?.aborted).toBe(false);
    expect(current.requestsWithMethod("GET")).toHaveLength(1);
    await act(async () => fresh.resolve(json([{ ...failedRun, outcome: "completed" }])));
    expect(await screen.findByText("completed")).toBeVisible();
  });
});

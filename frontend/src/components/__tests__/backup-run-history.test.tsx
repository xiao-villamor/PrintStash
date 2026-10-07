/* Backup history preserves successful copies and retries only the exact failed result. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BackupRunHistory } from "@/components/backup-run-history";
import type { BackupRun } from "@/lib/api/backup";
import * as backupApi from "@/lib/api/backup";
import * as taskCenter from "@/lib/task-center";
import { AuthContext } from "@/lib/auth-context";
import { clearLogin } from "@/lib/auth-store";
import { aJob } from "@/test-support/factories";
import {
  adminSession,
  memberSession,
  json,
  renderApp,
  type RouteTable,
} from "@/test-support/render";
import type { JobStatus } from "@/types";

/**
 * A retry is a Job: the POST only queues it, and the copy is published when
 * the Job completes. Ids are distinct per test because the Task Center keeps
 * a terminal Job's outcome for the life of the module.
 */
function retryAccepted(jobId: string) {
  return { job_id: jobId, state: "queued", message: "queued" };
}

function retryJob(job: Partial<JobStatus> & { job_id: string }): RouteTable {
  const status = aJob({ kind: "backups.retry_destination", model_id: null, file_id: null, ...job });
  return {
    "GET /api/v1/jobs": json([status]),
    [`GET /api/v1/jobs/${job.job_id}`]: json(status),
  };
}

function partialRun(): BackupRun {
  return {
    id: "run-1",
    backup_id: "archive-1",
    outcome: "partial",
    created_at: "2026-01-01T00:00:00Z",
    archive_sha256: "a".repeat(64),
    destinations: [
      {
        id: "local-result",
        run_id: "run-1",
        name: "Local backup",
        kind: "local",
        outcome: "completed",
        error_code: null,
        published_at: "2026-01-01T00:00:00Z",
        verified_at: null,
      },
      {
        id: "remote-result",
        run_id: "run-1",
        name: "Offsite S3",
        kind: "connection",
        outcome: "failed",
        error_code: "backup_remote_publication_failed",
        published_at: null,
        verified_at: null,
      },
    ],
  };
}

describe("Backup run history", () => {
  it.each([
    { label: "interrupted publication", error: "backup_publication_interrupted" },
    { label: "refused retry", error: "backup_retry_new_backup_required" },
    { label: "missing error detail", error: null },
  ])("prevents retry without an archive record: $label", async ({ error }) => {
    const run = partialRun();
    run.outcome = "failed";
    run.archive_sha256 = null;
    run.destinations = run.destinations
      .filter((destination) => destination.outcome === "failed")
      .map((destination) => ({ ...destination, error_code: error }));
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([run]) },
    });

    const retry = await screen.findByRole("button", { name: "Retry this destination" });

    expect(retry).toBeDisabled();
    expect(
      screen.getByText("This run has no verified source available for retry. Create a new backup."),
    ).toBeVisible();
    await userEvent.click(retry);
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("keeps the surviving copy visible during partial failure", async () => {
    renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([partialRun()]) },
    });
    const run = await screen.findByRole("article", { name: "archive-1: Partially completed" });
    expect(within(run).getByText("Local backup · Published")).toBeVisible();
    expect(within(run).getByText("Offsite S3 · Failed")).toBeVisible();
    expect(within(run).getAllByText("Not verified yet")).toHaveLength(2);
    expect(within(run).getAllByRole("button", { name: "Retry this destination" })).toHaveLength(1);
  });

  it("refreshes verified status after retrying the exact failed destination", async () => {
    let run = partialRun();
    const published = vi.fn<() => void>();
    const app = renderApp(<BackupRunHistory onPublished={published} />, {
      routes: {
        "GET /api/v1/backups/runs": () => json([run]),
        "POST /api/v1/backups/runs/destinations/remote-result/retry": () => {
          run = {
            ...run,
            outcome: "completed",
            destinations: run.destinations.map((result) => ({
              ...result,
              outcome: "completed",
              error_code: null,
              verified_at: result.kind === "local" ? "2026-01-02T00:00:00Z" : null,
            })),
          };
          return json(retryAccepted("retry-published"), 202);
        },
        ...retryJob({ job_id: "retry-published" }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    expect(await screen.findByRole("article", { name: "archive-1: Completed" })).toBeVisible();
    expect(screen.getByText("Offsite S3 · Published")).toBeVisible();
    expect(screen.getByText(/Last verified:/)).toBeVisible();
    expect(published).toHaveBeenCalledOnce();
    expect(
      app
        .requestsWithMethod("POST")
        .filter((request) =>
          request.url.endsWith("/backups/runs/destinations/remote-result/retry"),
        ),
    ).toHaveLength(1);
    expect(
      app.requestsWithMethod("GET").filter((request) => request.url.endsWith("/backups/runs")),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "Retry this destination" }),
    ).not.toBeInTheDocument();
  });

  it("explains why a new archive is required after a failed retry", async () => {
    let run = partialRun();
    const published = vi.fn<() => void>();
    const app = renderApp(<BackupRunHistory onPublished={published} />, {
      routes: {
        "GET /api/v1/backups/runs": () => json([run]),
        "POST /api/v1/backups/runs/destinations/remote-result/retry": () => {
          run = {
            ...run,
            destinations: run.destinations.map((result) =>
              result.id === "remote-result"
                ? { ...result, error_code: "backup_retry_new_backup_required" }
                : result,
            ),
          };
          return json(retryAccepted("retry-refused"), 202);
        },
        ...retryJob({
          job_id: "retry-refused",
          state: "failed",
          error: "backup_retry_new_backup_required",
        }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    expect(
      await screen.findByText(
        "This run has no verified source available for retry. Create a new backup.",
      ),
    ).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry this destination" })).toBeEnabled(),
    );
    expect(published).not.toHaveBeenCalled();
    expect(
      app
        .requestsWithMethod("POST")
        .filter((request) =>
          request.url.endsWith("/backups/runs/destinations/remote-result/retry"),
        ),
    ).toHaveLength(1);
    expect(
      app.requestsWithMethod("GET").filter((request) => request.url.endsWith("/backups/runs")),
    ).toHaveLength(2);
  });

  it("reports a retry another request already started", async () => {
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: {
        "GET /api/v1/backups/runs": () => json([partialRun()]),
        "POST /api/v1/backups/runs/destinations/remote-result/retry": () =>
          json({ detail: "backup_retry_in_progress" }, 409),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    expect(
      await screen.findByText("A retry is already in progress for this destination."),
    ).toBeVisible();
    expect(
      app
        .requestsWithMethod("POST")
        .filter((request) =>
          request.url.endsWith("/backups/runs/destinations/remote-result/retry"),
        ),
    ).toHaveLength(1);
  });

  it("keeps historical archives available when no runs exist", async () => {
    renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([]) },
    });
    expect(await screen.findByText(/Older archives remain available below/)).toBeVisible();
  });

  it("reports an unavailable execution history", async () => {
    renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json({ detail: "unavailable" }, 503) },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Backup runs could not be loaded.");
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  taskCenter.resetTasksForNewSetup();
});

describe("Backup history ownership", () => {
  it("refreshes backup run history when returning", async () => {
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([partialRun()]) },
    });
    await screen.findByRole("article", { name: "archive-1: Partially completed" });
    app.rerender(<p>Other view</p>);
    app.route({ "GET /api/v1/backups/runs": json([{ ...partialRun(), outcome: "completed" }]) });
    app.rerender(<BackupRunHistory onPublished={vi.fn<() => void>()} />);
    expect(await screen.findByRole("article", { name: "archive-1: Completed" })).toBeVisible();
    expect(app.requestsWithMethod("GET")).toHaveLength(2);
  });

  it("keeps one canonical history snapshot after invalidation", async () => {
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([partialRun()]) },
    });
    await screen.findByRole("article", { name: "archive-1: Partially completed" });
    const completed = { ...partialRun(), outcome: "completed" as const };
    app.route({ "GET /api/v1/backups/runs": json([completed]) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["backup-runs"] });
    });
    await screen.findByRole("article", { name: "archive-1: Completed" });
    expect(
      app.client
        .getQueryCache()
        .findAll({ queryKey: ["backup-runs"] })
        .map((query) => query.queryKey),
    ).toEqual([["backup-runs"]]);
    expect(app.requestsWithMethod("GET")).toHaveLength(2);
  });
  it("cancels a disposed history read", async () => {
    let signal: AbortSignal | null | undefined;
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: {
        "GET /api/v1/backups/runs": (_url, init) => {
          signal = init?.signal;
          return new Promise(() => {});
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    expect(signal?.aborted).toBe(true);
  });
  it("recovers an unavailable history without an empty-state claim", async () => {
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json({ detail: "unavailable" }, 503) },
    });
    await screen.findByRole("alert");
    expect(screen.queryByText(/Older archives remain available below/)).not.toBeInTheDocument();
    app.route({ "GET /api/v1/backups/runs": json([partialRun()]) });
    await userEvent.click(screen.getByRole("button", { name: "Refresh backup runs" }));
    expect(
      await screen.findByRole("article", { name: "archive-1: Partially completed" }),
    ).toBeVisible();
  });
  it("hides denied cached history", async () => {
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([partialRun()]) },
    });
    await screen.findByRole("article", { name: "archive-1: Partially completed" });
    app.route({ "GET /api/v1/backups/runs": json({ detail: "forbidden" }, 403) });
    await userEvent.click(screen.getByRole("button", { name: "Refresh backup runs" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry this destination" }),
    ).not.toBeInTheDocument();
  });
  it("hides private history immediately after role loss", async () => {
    const app = renderApp(
      <AuthContext.Provider value={adminSession()}>
        <BackupRunHistory onPublished={vi.fn<() => void>()} />
      </AuthContext.Provider>,
      {
        routes: { "GET /api/v1/backups/runs": json([partialRun()]) },
      },
    );
    await screen.findByRole("article", { name: "archive-1: Partially completed" });
    app.rerender(
      <AuthContext.Provider value={memberSession()}>
        <BackupRunHistory onPublished={vi.fn<() => void>()} />
      </AuthContext.Provider>,
    );
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    const refresh = screen.getByRole("button", { name: "Refresh backup runs" });
    expect(refresh).toBeDisabled();
    await userEvent.click(refresh);
    expect(app.requestsWithMethod("GET")).toHaveLength(1);
  });
  it("preserves transient-failure history read-only until recovery", async () => {
    const app = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([partialRun()]) },
    });
    await screen.findByRole("article", { name: "archive-1: Partially completed" });
    app.route({ "GET /api/v1/backups/runs": json({ detail: "unavailable" }, 503) });
    await userEvent.click(screen.getByRole("button", { name: "Refresh backup runs" }));
    await screen.findByRole("alert");
    expect(screen.getByText("Local backup · Published")).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry this destination" })).toBeDisabled();
    app.route({ "GET /api/v1/backups/runs": json([partialRun()]) });
    await userEvent.click(screen.getByRole("button", { name: "Refresh backup runs" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry this destination" })).toBeEnabled(),
    );
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("does not register a retired acceptance in the new account", async () => {
    const received = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const native = backupApi.retryBackupDestination;
    vi.spyOn(backupApi, "retryBackupDestination").mockImplementation(async (...args) => {
      const accepted = await native(...args);
      received.resolve();
      await release.promise;
      return accepted;
    });
    const published = vi.fn<() => void>();
    renderApp(<BackupRunHistory onPublished={published} />, {
      routes: {
        "GET /api/v1/backups/runs": json([partialRun()]),
        "POST /api/v1/backups/runs/destinations/remote-result/retry": json(
          retryAccepted("retired-acceptance"),
          202,
        ),
        ...retryJob({ job_id: "retired-acceptance" }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    await received.promise;
    await act(async () => {
      clearLogin();
      taskCenter.resetTasksForNewSetup();
    });
    await act(async () => release.resolve());
    expect(taskCenter.listTasks().some((task) => task.jobId === "retired-acceptance")).toBe(false);
    expect(published).not.toHaveBeenCalled();
    expect(screen.queryByText("Backup copy published.")).not.toBeInTheDocument();
  });
  it("preserves accepted durable tracking after local disposal", async () => {
    const received = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const native = backupApi.retryBackupDestination;
    vi.spyOn(backupApi, "retryBackupDestination").mockImplementation(async (...args) => {
      const accepted = await native(...args);
      received.resolve();
      await release.promise;
      return accepted;
    });
    const published = vi.fn<() => void>();
    const app = renderApp(<BackupRunHistory onPublished={published} />, {
      routes: {
        "GET /api/v1/backups/runs": json([partialRun()]),
        "POST /api/v1/backups/runs/destinations/remote-result/retry": json(
          retryAccepted("disposed-accepted"),
          202,
        ),
        ...retryJob({ job_id: "disposed-accepted" }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    await received.promise;
    app.unmount();
    await act(async () => release.resolve());
    await waitFor(() =>
      expect(taskCenter.listTasks().some((task) => task.jobId === "disposed-accepted")).toBe(true),
    );
    expect(published).not.toHaveBeenCalled();
  });
  it("does not publish a disposed terminal retry into a new view", async () => {
    const terminal = Promise.withResolvers<void>();
    const release = Promise.withResolvers<void>();
    const native = taskCenter.waitForImportJob;
    vi.spyOn(taskCenter, "waitForImportJob").mockImplementation(async (...args) => {
      const job = await native(...args);
      terminal.resolve();
      await release.promise;
      return job;
    });
    const published = vi.fn<() => void>();
    const app = renderApp(<BackupRunHistory onPublished={published} />, {
      routes: {
        "GET /api/v1/backups/runs": json([partialRun()]),
        "POST /api/v1/backups/runs/destinations/remote-result/retry": json(
          retryAccepted("disposed-terminal"),
          202,
        ),
        ...retryJob({ job_id: "disposed-terminal" }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    await terminal.promise;
    app.unmount();
    let signal: AbortSignal | null | undefined;
    const fresh = Promise.withResolvers<Response>();
    const current = renderApp(<BackupRunHistory onPublished={vi.fn<() => void>()} />, {
      routes: {
        "GET /api/v1/backups/runs": (_url, init) => {
          signal = init?.signal;
          return fresh.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    await act(async () => release.resolve());
    expect(published).not.toHaveBeenCalled();
    expect(signal?.aborted).toBe(false);
    expect(current.requestsWithMethod("GET")).toHaveLength(1);
    expect(screen.queryByText("Backup copy published.")).not.toBeInTheDocument();
    await act(async () => fresh.resolve(json([partialRun()])));
  });
});

/* Backup history preserves successful copies and retries only the exact failed result. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BackupRunHistory } from "@/components/backup-run-history";
import type { BackupRun } from "@/lib/api/backup";
import { aJob } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";
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
    const app = renderApp(<BackupRunHistory refreshKey={0} onPublished={vi.fn<() => void>()} />, {
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
    renderApp(<BackupRunHistory refreshKey={0} onPublished={vi.fn<() => void>()} />, {
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
    renderApp(<BackupRunHistory refreshKey={0} onPublished={published} />, {
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
      screen.queryByRole("button", { name: "Retry this destination" }),
    ).not.toBeInTheDocument();
  });

  it("explains why a new archive is required after a failed retry", async () => {
    let run = partialRun();
    const published = vi.fn<() => void>();
    renderApp(<BackupRunHistory refreshKey={0} onPublished={published} />, {
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
  });

  it("reports a retry another request already started", async () => {
    renderApp(<BackupRunHistory refreshKey={0} onPublished={vi.fn<() => void>()} />, {
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
  });

  it("keeps historical archives available when no runs exist", async () => {
    renderApp(<BackupRunHistory refreshKey={0} onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json([]) },
    });
    expect(await screen.findByText(/Older archives remain available below/)).toBeVisible();
  });

  it("reports an unavailable execution history", async () => {
    renderApp(<BackupRunHistory refreshKey={0} onPublished={vi.fn<() => void>()} />, {
      routes: { "GET /api/v1/backups/runs": json({ detail: "unavailable" }, 503) },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Backup runs could not be loaded.");
  });
});

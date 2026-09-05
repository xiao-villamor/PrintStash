import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BackupRunHistory } from "@/components/backup-run-history";
import type { BackupRun } from "@/lib/api/backup";
import { json, renderApp } from "@/test-support/render";

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

  it("retries only the failed destination and refreshes verified status", async () => {
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
          return json(run.destinations[1]);
        },
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

  it("explains why a new archive is required after a refused retry", async () => {
    let run = partialRun();
    renderApp(<BackupRunHistory refreshKey={0} onPublished={vi.fn<() => void>()} />, {
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
          return json({ detail: "backup_retry_new_backup_required" }, 409);
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Retry this destination" }));
    expect(
      await screen.findByText("No verified copy survives. Create a new backup."),
    ).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry this destination" })).toBeEnabled(),
    );
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

/**
 * Settings → Background work, the administrator's view of every Job-running
 * process.
 *
 * The destructive actions are the ones that matter: cancelling a definition's
 * queue withdraws work other users asked for, and regenerating a derivative
 * kind re-renders the whole library. Both ask first. A lane override is sent
 * as the number typed, and a reset sends `null` explicitly, because omitting
 * the field would leave the override in place. A Job change reported on the
 * events socket refreshes the page, since a worker in another process moved it.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackgroundWorkPanel, type BackgroundWorkApi } from "@/components/background-work-panel";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { setLocale } from "@/lib/locale";
import { aJob, aWorkOverview } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import type { JobStatus, WorkOverview } from "@/types";

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();
}

let socket: FakeSocket;

function stubApi(overview: WorkOverview = aWorkOverview(), over: Partial<BackgroundWorkApi> = {}) {
  return {
    overview: vi.fn<BackgroundWorkApi["overview"]>().mockResolvedValue(overview),
    jobs: vi.fn<BackgroundWorkApi["jobs"]>().mockResolvedValue([]),
    cancelJob: vi
      .fn<BackgroundWorkApi["cancelJob"]>()
      .mockImplementation(async (jobId): Promise<JobStatus> =>
        aJob({ job_id: jobId, state: "cancelled" }),
      ),
    setLane: vi.fn<BackgroundWorkApi["setLane"]>().mockResolvedValue(overview),
    cancelQueued: vi.fn<BackgroundWorkApi["cancelQueued"]>().mockResolvedValue({ cancelled: 2 }),
    regenerate: vi
      .fn<BackgroundWorkApi["regenerate"]>()
      .mockImplementation(async (kind, mode) => ({ kind, mode })),
    retry: vi
      .fn<BackgroundWorkApi["retry"]>()
      .mockImplementation(async (jobId): Promise<JobStatus> => aJob({ job_id: jobId })),
    ...over,
  } satisfies BackgroundWorkApi;
}

function renderPanel(api: BackgroundWorkApi) {
  return renderApp(<BackgroundWorkPanel api={api} />);
}

async function openAdvanced(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText("Advanced controls"));
}

/** An overview with a queue to cancel and a failure to retry. */
function busyOverview(): WorkOverview {
  const base = aWorkOverview();
  return {
    ...base,
    definitions: [{ ...base.definitions[0], queued: 4, running: 1, failed: 1 }],
    failed_jobs: [
      aJob({
        job_id: "failed-1",
        kind: "derivatives.mesh",
        label: "Mesh derivatives",
        state: "failed",
        error: "backup_blob_missing",
        retryable: true,
      }),
    ],
    failed_derivatives: 3,
  };
}

beforeEach(() => {
  socket = new FakeSocket();
  setEventSocketFactory(async () => socket);
});

afterEach(() => {
  vi.useRealTimers();
  setLocale("en");
});

describe("BackgroundWorkPanel", () => {
  it("shows each active job's progress", async () => {
    const job = aJob({
      job_id: "preview-1",
      label: "Model images",
      state: "running",
      stage: "inspecting",
      progress: 45,
      processed: 2,
      total: 4,
    });
    renderPanel(
      stubApi(aWorkOverview(), {
        jobs: vi.fn<BackgroundWorkApi["jobs"]>().mockResolvedValue([job]),
      }),
    );

    expect(await screen.findByRole("progressbar", { name: "Model images" })).toBeVisible();
    expect(screen.getByText(/2 \/ 4 · 45%/)).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "Model images" })).toHaveAttribute(
      "aria-valuenow",
      "45",
    );
  });

  it("links a preview job to its model", async () => {
    renderPanel(
      stubApi(aWorkOverview(), {
        jobs: vi.fn<BackgroundWorkApi["jobs"]>().mockResolvedValue([
          aJob({
            job_id: "preview-model",
            label: "Model images",
            state: "running",
            model_id: 17,
          }),
        ]),
      }),
    );

    expect(await screen.findByRole("link", { name: "Open model" })).toHaveAttribute(
      "href",
      "/models/17",
    );
  });

  it("cancels the selected active job after confirmation", async () => {
    const user = userEvent.setup();
    const job = aJob({ job_id: "preview-2", label: "Model images", state: "running" });
    const api = stubApi(aWorkOverview(), {
      jobs: vi.fn<BackgroundWorkApi["jobs"]>().mockResolvedValue([job]),
    });
    renderPanel(api);

    await user.click(await screen.findByRole("button", { name: "Cancel job" }));
    expect(api.cancelJob).not.toHaveBeenCalled();
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel job" }),
    );
    await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith("preview-2"));
  });

  it("leads to a model's preview status", async () => {
    renderPanel(stubApi());

    expect(await screen.findByRole("heading", { name: "What's happening now" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Checking a model's preview?" })).toBeVisible();
    expect(screen.getByText(/Open the model and look in Files/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Browse models" })).toHaveAttribute("href", "/");
  });

  it("explains the preview path in Spanish", async () => {
    renderApp(<BackgroundWorkPanel api={stubApi()} />, { locale: "es" });

    expect(
      await screen.findByRole("heading", { name: "¿Buscas la vista previa de un modelo?" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Explorar modelos" })).toHaveAttribute("href", "/");
  });

  it("explains when nothing is in progress", async () => {
    renderPanel(stubApi());

    expect(await screen.findByText("Nothing is running or waiting right now.")).toBeVisible();
  });

  it("starts with advanced controls closed", async () => {
    renderPanel(stubApi());

    expect(await screen.findByText("Advanced controls")).toBeVisible();
    expect(screen.queryByLabelText("Concurrency for derive.native")).not.toBeInTheDocument();
    expect(screen.getByText("Advanced controls").closest("details")).not.toHaveAttribute("open");
  });

  it("explains when no work has failed", async () => {
    renderPanel(stubApi());

    expect(await screen.findByText("No recent failures.")).toBeVisible();
  });

  it("shows running work before the technical controls", async () => {
    renderPanel(
      stubApi(busyOverview(), {
        jobs: vi
          .fn<BackgroundWorkApi["jobs"]>()
          .mockResolvedValue([
            aJob({ job_id: "active-preview", label: "Preparing model image", state: "running" }),
            aJob({ job_id: "waiting-scan", label: "Scanning library", state: "queued" }),
          ]),
      }),
    );

    expect(await screen.findByText("Preparing model image")).toBeVisible();
    expect(screen.getByText("Scanning library")).toBeVisible();
    expect(screen.getByRole("heading", { name: "In progress" })).toBeVisible();
  });

  it("flags a process that stopped heartbeating", async () => {
    const user = userEvent.setup();
    const overview = aWorkOverview();
    overview.executors = [{ ...overview.executors[0], stale: true }];

    renderPanel(stubApi(overview));

    expect(await screen.findByRole("alert")).toHaveTextContent("Workers not responding: 1.");
    await user.click(screen.getByRole("button", { name: "View workers" }));
    expect(screen.getByText("Not responding")).toBeVisible();
  });

  it("keeps worker controls in their own view", async () => {
    const user = userEvent.setup();
    renderPanel(stubApi());

    await openAdvanced(user);
    await user.click(await screen.findByRole("tab", { name: "Worker settings" }));

    expect(screen.getByLabelText("Concurrency for derive.native")).toHaveValue(1);
    expect(screen.getByText("host")).toBeVisible();
    expect(screen.getByText("Healthy")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Derive missing" })).not.toBeInTheDocument();
  });

  it("reports an overview it cannot load", async () => {
    renderPanel(
      stubApi(aWorkOverview(), {
        overview: vi.fn<BackgroundWorkApi["overview"]>().mockRejectedValue(new Error("offline")),
      }),
    );

    expect(await screen.findByRole("alert")).toBeVisible();
  });

  describe("lanes", () => {
    it("sets a lane's concurrency to the number typed", async () => {
      const user = userEvent.setup();
      const api = stubApi();
      renderPanel(api);
      await openAdvanced(user);
      await user.click(await screen.findByRole("tab", { name: "Worker settings" }));
      const input = screen.getByLabelText("Concurrency for derive.native");

      await user.clear(input);
      await user.type(input, "3");
      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => expect(api.setLane).toHaveBeenCalledWith("derive.native", 3));
    });

    it("returns an overridden lane to its default", async () => {
      const user = userEvent.setup();
      const overview = aWorkOverview();
      overview.lanes = [{ ...overview.lanes[0], concurrency: 4, overridden: true }];
      const api = stubApi(overview);
      renderPanel(api);

      await openAdvanced(user);
      await user.click(await screen.findByRole("tab", { name: "Worker settings" }));
      await user.click(screen.getByRole("button", { name: "Reset" }));

      await waitFor(() => expect(api.setLane).toHaveBeenCalledWith("derive.native", null));
    });

    it.each([
      { label: "zero", value: "0" },
      { label: "more than 64", value: "65" },
      { label: "a fraction", value: "1.5" },
    ])("refuses $label", async ({ value }) => {
      const user = userEvent.setup();
      renderPanel(stubApi());
      await openAdvanced(user);
      await user.click(await screen.findByRole("tab", { name: "Worker settings" }));
      const input = screen.getByLabelText("Concurrency for derive.native");

      await user.clear(input);
      await user.type(input, value);

      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    });
  });

  describe("queues", () => {
    it("cancels a definition's queued Jobs only after confirmation", async () => {
      const user = userEvent.setup();
      const api = stubApi(busyOverview());
      renderPanel(api);

      await openAdvanced(user);
      await user.click(await screen.findByRole("button", { name: "Cancel queued" }));
      expect(api.cancelQueued).not.toHaveBeenCalled();
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Cancel queued" }));

      await waitFor(() => expect(api.cancelQueued).toHaveBeenCalledWith("derivatives.mesh"));
    });

    it("offers no cancel for a definition with nothing queued", async () => {
      const user = userEvent.setup();
      renderPanel(stubApi());

      await openAdvanced(user);

      expect(screen.queryByRole("button", { name: "Cancel queued" })).not.toBeInTheDocument();
    });
  });

  describe("derivatives", () => {
    it("derives only what is missing without asking", async () => {
      const user = userEvent.setup();
      const api = stubApi();
      renderPanel(api);

      await openAdvanced(user);
      const row = (await screen.findAllByText("thumbnail"))[0].closest("li")!;
      await user.click(within(row).getByRole("button", { name: "Derive missing" }));

      await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith("thumbnail", "missing"));
    });

    it("regenerates every Artifact only after confirmation", async () => {
      const user = userEvent.setup();
      const api = stubApi();
      renderPanel(api);

      await openAdvanced(user);
      const row = (await screen.findAllByText("thumbnail"))[0].closest("li")!;
      await user.click(within(row).getByRole("button", { name: "Regenerate all" }));
      expect(api.regenerate).not.toHaveBeenCalled();
      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Regenerate" }),
      );

      await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith("thumbnail", "all"));
    });
  });

  describe("failures", () => {
    it("explains why a Job failed", async () => {
      renderPanel(stubApi(busyOverview()));

      expect(
        await screen.findByText(
          "A file needed for the backup is missing. Check the storage and try again.",
        ),
      ).toBeVisible();
    });

    it("retries a failed Job", async () => {
      const user = userEvent.setup();
      const api = stubApi(busyOverview());
      renderPanel(api);

      await user.click(await screen.findByRole("button", { name: "Retry" }));

      await waitFor(() => expect(api.retry).toHaveBeenCalledWith("failed-1"));
    });

    it("counts failed derivatives across the library", async () => {
      renderPanel(stubApi(busyOverview()));

      expect(
        await screen.findByText(/Preview or metadata failures across the library: 3/),
      ).toBeVisible();
    });
  });

  describe("freshness", () => {
    it("refreshes when a Job changes on the server", async () => {
      const api = stubApi();
      renderPanel(api);
      await screen.findByRole("heading", { name: "What's happening now" });
      await waitFor(() => expect(socket.onmessage).not.toBeNull());

      socket.onmessage?.({
        data: JSON.stringify({
          type: "job",
          job_id: "j",
          kind: "derivatives.mesh",
          state: "completed",
        }),
      });

      await waitFor(() => expect(api.overview).toHaveBeenCalledTimes(2));
    });

    it("stops listening when it leaves the page", async () => {
      const { unmount } = renderPanel(stubApi());
      await screen.findByRole("heading", { name: "What's happening now" });
      await waitFor(() => expect(socket.onmessage).not.toBeNull());

      unmount();

      expect(socket.close).toHaveBeenCalled();
    });
  });
});

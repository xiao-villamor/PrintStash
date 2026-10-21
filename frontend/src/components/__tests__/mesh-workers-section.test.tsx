/**
 * Settings → Background work → Worker settings → Mesh processing.
 *
 * The administrator sees the running count, where it comes from, what the
 * automatic rule would pick and how many fit in memory. A typed count is sent
 * with the editing base it was read under; "Use default" clears the stored
 * count. A count above what memory fits cannot be saved, and a concurrent
 * edit reloads the current value instead of overwriting it.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MeshWorkersSection } from "@/components/mesh-workers-section";
import type { meshWorkersApi } from "@/lib/api/mesh-workers";
import { ApiError } from "@/lib/errors";
import { aMeshWorkers } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";

type Api = typeof meshWorkersApi;

function stubApi(over: Partial<Api> = {}): Api {
  return {
    read: vi.fn<Api["read"]>().mockResolvedValue(aMeshWorkers()),
    save: vi
      .fn<Api["save"]>()
      .mockImplementation(async (jobs) =>
        aMeshWorkers({ edit_version: 2, jobs, configured: jobs, source: "database" }),
      ),
    reset: vi.fn<Api["reset"]>().mockResolvedValue(aMeshWorkers({ edit_version: 2 })),
    ...over,
  };
}

describe("MeshWorkersSection", () => {
  it("shows the automatic count with its memory limit", async () => {
    renderApp(<MeshWorkersSection api={stubApi()} />);

    expect(await screen.findByLabelText("Models processed at once")).toHaveValue(3);
    expect(screen.getByText("Automatic")).toBeVisible();
    expect(
      screen.getByText(
        "Automatic picks 3 for this machine's CPUs and memory; up to 4 fit its memory.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Use default" })).not.toBeInTheDocument();
  });

  it("saves a chosen count with the base it was read under", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    renderApp(<MeshWorkersSection api={api} />);
    const input = await screen.findByLabelText("Models processed at once");

    await user.clear(input);
    await user.type(input, "2");
    await user.click(screen.getByRole("button", { name: "Save models processed at once" }));

    await waitFor(() =>
      expect(api.save).toHaveBeenCalledWith(
        2,
        expect.objectContaining({
          base: { edit_epoch: "00000000000000000000000000000001", edit_version: 1 },
        }),
      ),
    );
    expect(await screen.findByText("Chosen by an administrator")).toBeVisible();
  });

  it("returns a stored count to the default", async () => {
    const user = userEvent.setup();
    const api = stubApi({
      read: vi
        .fn<Api["read"]>()
        .mockResolvedValue(aMeshWorkers({ jobs: 1, configured: 1, source: "database" })),
    });
    renderApp(<MeshWorkersSection api={api} />);

    await user.click(await screen.findByRole("button", { name: "Use default" }));

    await waitFor(() => expect(api.reset).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Automatic")).toBeVisible();
  });

  it("cannot save more than memory fits", async () => {
    const user = userEvent.setup();
    const api = stubApi();
    renderApp(<MeshWorkersSection api={api} />);
    const input = await screen.findByLabelText("Models processed at once");

    await user.clear(input);
    await user.type(input, "5");

    expect(screen.getByRole("button", { name: "Save models processed at once" })).toBeDisabled();
  });

  it("names the environment variable that set the count", async () => {
    renderApp(
      <MeshWorkersSection
        api={stubApi({
          read: vi
            .fn<Api["read"]>()
            .mockResolvedValue(aMeshWorkers({ jobs: 2, source: "environment" })),
        })}
      />,
    );

    expect(await screen.findByText("Set by VAULT_MAX_RENDER_JOBS")).toBeVisible();
  });

  it("tells a split deployment that workers apply it on restart", async () => {
    renderApp(
      <MeshWorkersSection
        api={stubApi({
          read: vi.fn<Api["read"]>().mockResolvedValue(aMeshWorkers({ restart_required: true })),
        })}
      />,
    );

    expect(
      await screen.findByText("Separate worker processes apply a change when they restart."),
    ).toBeVisible();
  });

  it("reloads the current value after a concurrent edit", async () => {
    const user = userEvent.setup();
    const read = vi
      .fn<Api["read"]>()
      .mockResolvedValueOnce(aMeshWorkers())
      .mockResolvedValue(
        aMeshWorkers({ edit_version: 5, jobs: 4, configured: 4, source: "database" }),
      );
    const api = stubApi({
      read,
      save: vi
        .fn<Api["save"]>()
        .mockRejectedValue(new ApiError(412, "edit_conflict", "edit_conflict")),
    });
    renderApp(<MeshWorkersSection api={api} />);
    const input = await screen.findByLabelText("Models processed at once");

    await user.clear(input);
    await user.type(input, "2");
    await user.click(screen.getByRole("button", { name: "Save models processed at once" }));

    await waitFor(() => expect(input).toHaveValue(4));
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("reports a count it cannot load", async () => {
    renderApp(
      <MeshWorkersSection
        api={stubApi({ read: vi.fn<Api["read"]>().mockRejectedValue(new Error("offline")) })}
      />,
    );

    expect(await screen.findByText("The mesh job count could not be loaded.")).toBeVisible();
  });
});

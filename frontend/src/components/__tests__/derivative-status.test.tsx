/**
 * An Artifact row says what is still being derived from it and what failed.
 *
 * A fresh upload has no preview yet; the row must read as "preparing", not as
 * broken, and must say so rather than hiding the kind, because a missing
 * derivative is unknown, not absent. A failure names its reason; only an
 * editor may retry it, since a retry spends the server's render capacity. A
 * notice about this Artifact on the Model's channel refreshes the row.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DerivativeStatus, type DerivativeStatusApi } from "@/components/derivative-status";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { aDerivative } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import type { DerivativeRead } from "@/types";

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();
}

let socket: FakeSocket;

function stubApi(derivatives: DerivativeRead[]) {
  return {
    list: vi.fn<DerivativeStatusApi["list"]>().mockResolvedValue(derivatives),
    retry: vi
      .fn<DerivativeStatusApi["retry"]>()
      .mockResolvedValue([aDerivative({ kind: "thumbnail", state: "queued" })]),
  } satisfies DerivativeStatusApi;
}

function renderStatus(api: DerivativeStatusApi, canRetry = true) {
  return renderApp(<DerivativeStatus modelId={3} fileId={7} canRetry={canRetry} api={api} />);
}

const FAILED = aDerivative({
  kind: "thumbnail",
  state: "failed",
  failure_reason: "backup_blob_missing",
  retryable: true,
});

beforeEach(() => {
  socket = new FakeSocket();
  setEventSocketFactory(async () => socket);
});

describe("DerivativeStatus", () => {
  it("says which outputs are still being prepared", async () => {
    renderStatus(
      stubApi([
        aDerivative({ kind: "metadata", state: "running" }),
        aDerivative({ kind: "thumbnail", state: "pending" }),
      ]),
    );

    expect(await screen.findByRole("status")).toHaveTextContent("Preparing Metadata, Preview…");
  });

  it("renders nothing once every output is finished", async () => {
    const api = stubApi([
      aDerivative({ kind: "metadata" }),
      aDerivative({ kind: "thumbnail", state: "skipped" }),
    ]);
    renderStatus(api);

    await waitFor(() => expect(api.list).toHaveBeenCalled());

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(/failed/)).not.toBeInTheDocument();
  });

  it("names why an output failed", async () => {
    renderStatus(stubApi([FAILED]));

    expect(
      await screen.findByText(/Preview failed: A file needed for the backup is missing/),
    ).toBeVisible();
  });

  it("lets an editor retry a failed output", async () => {
    const user = userEvent.setup();
    const api = stubApi([FAILED]);
    renderStatus(api);

    await user.click(await screen.findByRole("button", { name: "Retry Preview" }));

    await waitFor(() => expect(api.retry).toHaveBeenCalledWith(7, "thumbnail"));
    expect(await screen.findByRole("status")).toHaveTextContent("Preparing Preview…");
  });

  it("offers a viewer no retry", async () => {
    renderStatus(stubApi([FAILED]), false);

    await screen.findByText(/Preview failed/);

    expect(screen.queryByRole("button", { name: "Retry Preview" })).not.toBeInTheDocument();
  });

  it("offers no retry for a failure a retry cannot fix", async () => {
    renderStatus(stubApi([{ ...FAILED, retryable: false }]));

    await screen.findByText(/Preview failed/);

    expect(screen.queryByRole("button", { name: "Retry Preview" })).not.toBeInTheDocument();
  });

  it("refreshes when this Artifact's derivative changes", async () => {
    const api = stubApi([aDerivative({ kind: "thumbnail", state: "running" })]);
    renderStatus(api);
    await screen.findByRole("status");
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    api.list.mockResolvedValue([aDerivative({ kind: "thumbnail" })]);

    socket.onmessage?.({
      data: JSON.stringify({
        type: "derivative",
        model_id: 3,
        file_id: 7,
        kind: "thumbnail",
        state: "ready",
      }),
    });

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("ignores another Artifact of the same Model", async () => {
    const api = stubApi([aDerivative({ kind: "thumbnail", state: "running" })]);
    renderStatus(api);
    await screen.findByRole("status");
    await waitFor(() => expect(socket.onmessage).not.toBeNull());

    socket.onmessage?.({
      data: JSON.stringify({
        type: "derivative",
        model_id: 3,
        file_id: 8,
        kind: "thumbnail",
        state: "ready",
      }),
    });

    expect(api.list).toHaveBeenCalledTimes(1);
  });
});

describe("disabled derivatives", () => {
  it("shows disabled work without a spinner or retry", async () => {
    const { container } = renderStatus(
      stubApi([aDerivative({ state: "disabled", retryable: false })]),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Processing disabled");
    expect(container.querySelector(".animate-spin")).toBeNull();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/failed/)).not.toBeInTheDocument();
  });

  it.each(["derivative_policy", "resync"])("refreshes on %s", async (type) => {
    const api = stubApi([aDerivative({ state: "disabled" })]);
    renderStatus(api);
    await screen.findByRole("status");
    await waitFor(() => expect(socket.onmessage).not.toBeNull());
    api.list.mockResolvedValue([aDerivative({ state: "pending" })]);
    socket.onmessage?.({ data: JSON.stringify({ type }) });
    expect(await screen.findByText("Preparing Preview…")).toBeVisible();
  });
});

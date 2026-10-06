/** Settled derivatives refresh the authorized detail without competing local copies. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDerivativeRefresh } from "../use-derivative-refresh";
import { useModelDetail } from "@/features/library/model-detail";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { aModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();
}
let socket: FakeSocket;
const initialModel = aModel({ id: 3, name: "Initial", edit_version: 1 });
function Probe() {
  const model = useModelDetail(3, initialModel);
  useDerivativeRefresh(3);
  return <p>{model.data?.name}</p>;
}
async function subscribed() {
  await waitFor(() => expect(socket.onmessage).not.toBeNull());
  act(() => socket.onopen?.());
}
function send(frame: { type: string; model_id?: number; state?: string }) {
  act(() =>
    socket.onmessage?.({ data: JSON.stringify({ file_id: 1, kind: "thumbnail", ...frame }) }),
  );
}
beforeEach(() => {
  socket = new FakeSocket();
  setEventSocketFactory(async () => socket);
});
describe("useDerivativeRefresh", () => {
  it("follows the Model's channel", async () => {
    renderApp(<Probe />);

    await subscribed();

    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ subscribe: "model:3" }));
  });
  it.each(["ready", "skipped", "failed"])("refetches when a derivative is %s", async (state) => {
    renderApp(<Probe />, {
      routes: { "GET /api/v1/models/3": json(aModel({ id: 3, name: "Derived", edit_version: 2 })) },
    });
    await subscribed();

    send({ type: "derivative", model_id: 3, state });

    expect(await screen.findByText("Derived")).toBeVisible();
  });
  it("does not refetch for a derivative that only started", async () => {
    const view = renderApp(<Probe />);
    await subscribed();

    send({ type: "derivative", model_id: 3, state: "running" });

    expect(view.requests()).toEqual([]);
    expect(screen.getByText("Initial")).toBeVisible();
  });
  it("refetches after a resync", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/3": json(aModel({ id: 3, name: "Reconnected", edit_version: 2 })),
      },
    });
    await subscribed();

    send({ type: "resync" });

    expect(await screen.findByText("Reconnected")).toBeVisible();
  });
  it("coalesces settled derivative notices into one read", async () => {
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      routes: { "GET /api/v1/models/3": () => pending.promise },
    });
    await subscribed();
    send({ type: "derivative", model_id: 3, state: "ready" });
    await waitFor(() => expect(view.requests()).toHaveLength(1));

    send({ type: "resync" });
    send({ type: "derivative", model_id: 3, state: "ready" });
    await act(async () =>
      pending.resolve(json(aModel({ id: 3, name: "Derived", edit_version: 2 }))),
    );

    expect(await screen.findByText("Derived")).toBeVisible();
    expect(view.requests().map((request) => request.url)).toEqual(["/api/v1/models/3"]);
  });
  it("stops following when the view closes", async () => {
    const { unmount } = renderApp(<Probe />);
    await subscribed();

    unmount();

    expect(socket.close).toHaveBeenCalled();
  });
});

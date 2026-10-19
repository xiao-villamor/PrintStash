/** Settled derivatives refresh the authorized detail without competing local copies. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDerivativeRefresh } from "../use-derivative-refresh";
import { modelDetailOptions, useModelDetail } from "@/features/library/model-detail";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { aModel } from "@/test-support/factories";
import type { ModelRead } from "@/types";
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
function RefreshFollower() {
  useDerivativeRefresh(3);
  return null;
}
function LifetimeProbe() {
  const model = useModelDetail(3, initialModel);
  const [following, setFollowing] = useState(true);
  return (
    <>
      <p>{model.data?.name}</p>
      <button type="button" onClick={() => setFollowing(false)}>
        Stop following
      </button>
      {following && <RefreshFollower />}
    </>
  );
}
function PublicationProbe({ receipt }: { receipt: ModelRead }) {
  const model = useModelDetail(3, initialModel);
  useDerivativeRefresh(3);
  return (
    <>
      <p>{model.data?.name}</p>
      <button type="button" onClick={() => void model.publish(receipt)}>
        Confirm edit
      </button>
    </>
  );
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
function acknowledge(revision: string | undefined) {
  act(() =>
    socket.onmessage?.({
      data: JSON.stringify({ type: "subscribed", channel: "model:3", revision }),
    }),
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
  it("coalesces notices received during a read into one follow-up", async () => {
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      routes: { "GET /api/v1/models/3": () => pending.promise },
    });
    await subscribed();
    send({ type: "derivative", model_id: 3, state: "ready" });
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    view.route({
      "GET /api/v1/models/3": json(aModel({ id: 3, name: "Latest", edit_version: 3 })),
    });

    send({ type: "resync" });
    send({ type: "derivative", model_id: 3, state: "ready" });
    await act(async () =>
      pending.resolve(json(aModel({ id: 3, name: "Earlier", edit_version: 2 }))),
    );

    expect(await screen.findByText("Latest")).toBeVisible();
    expect(view.requests().map((request) => request.url)).toEqual([
      "/api/v1/models/3",
      "/api/v1/models/3",
    ]);
  });
  it("reads again when a notice joins an existing request", async () => {
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      routes: { "GET /api/v1/models/3": () => pending.promise },
    });
    await subscribed();
    const reading = view.client.fetchQuery({ ...modelDetailOptions(3), staleTime: 0 });
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    view.route({
      "GET /api/v1/models/3": json(aModel({ id: 3, name: "Latest", edit_version: 3 })),
    });

    send({ type: "derivative", model_id: 3, state: "ready" });
    await act(async () => {
      pending.resolve(json(aModel({ id: 3, name: "Earlier", edit_version: 2 })));
      await reading;
    });

    expect(await screen.findByText("Latest")).toBeVisible();
    expect(view.requests()).toHaveLength(2);
  });
  it("recovers a failed read when a resync is queued", async () => {
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      routes: { "GET /api/v1/models/3": () => pending.promise },
    });
    await subscribed();
    send({ type: "derivative", model_id: 3, state: "ready" });
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    view.route({
      "GET /api/v1/models/3": json(aModel({ id: 3, name: "Recovered", edit_version: 3 })),
    });

    send({ type: "resync" });
    await act(async () => pending.resolve(json({ detail: "temporarily_unavailable" }, 503)));

    expect(await screen.findByText("Recovered")).toBeVisible();
    expect(view.requests()).toHaveLength(2);
  });
  it("rechecks a cancelled refresh after a confirmed edit", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(
      <PublicationProbe receipt={aModel({ id: 3, name: "Confirmed", edit_version: 3 })} />,
      {
        routes: { "GET /api/v1/models/3": () => pending.promise },
      },
    );
    await subscribed();
    send({ type: "derivative", model_id: 3, state: "ready" });
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    view.route({
      "GET /api/v1/models/3": json(aModel({ id: 3, name: "Latest metadata", edit_version: 3 })),
    });

    await user.click(screen.getByRole("button", { name: "Confirm edit" }));
    await act(async () =>
      pending.resolve(json(aModel({ id: 3, name: "Earlier", edit_version: 2 }))),
    );

    expect(await screen.findByText("Latest metadata")).toBeVisible();
    expect(view.requests()).toHaveLength(2);
  });
  it("stops queued refreshes when their owner closes", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const view = renderApp(<LifetimeProbe />, {
      routes: { "GET /api/v1/models/3": () => pending.promise },
    });
    await subscribed();
    send({ type: "derivative", model_id: 3, state: "ready" });
    await waitFor(() => expect(view.requests()).toHaveLength(1));
    view.route({
      "GET /api/v1/models/3": json(aModel({ id: 3, name: "Late refresh", edit_version: 3 })),
    });
    send({ type: "resync" });

    await user.click(screen.getByRole("button", { name: "Stop following" }));
    await act(async () =>
      pending.resolve(json(aModel({ id: 3, name: "Earlier", edit_version: 2 }))),
    );

    expect(await screen.findByText("Earlier")).toBeVisible();
    expect(view.requests()).toHaveLength(1);
  });
  it("reads nothing when the subscription starts from the revision on screen", async () => {
    const view = renderApp(<Probe />);
    await subscribed();

    acknowledge(initialModel.derivatives_revision);

    expect(view.requests()).toEqual([]);
  });
  it("refetches when the subscription starts from a later revision", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/3": json(
          aModel({ id: 3, name: "Caught up", edit_version: 2, derivatives_revision: "1:7.5" }),
        ),
      },
    });
    await subscribed();

    acknowledge("1:7.5");

    expect(await screen.findByText("Caught up")).toBeVisible();
  });
  it("refetches when the acknowledgement names no revision", async () => {
    renderApp(<Probe />, {
      routes: {
        "GET /api/v1/models/3": json(aModel({ id: 3, name: "Older server", edit_version: 2 })),
      },
    });
    await subscribed();

    acknowledge(undefined);

    expect(await screen.findByText("Older server")).toBeVisible();
  });
  it("ignores another Model's acknowledgement", async () => {
    const view = renderApp(<Probe />);
    await subscribed();

    act(() =>
      socket.onmessage?.({
        data: JSON.stringify({ type: "subscribed", channel: "model:4", revision: "9:9.9" }),
      }),
    );

    expect(view.requests()).toEqual([]);
  });
  it("stops following when the view closes", async () => {
    const { unmount } = renderApp(<Probe />);
    await subscribed();

    unmount();

    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ unsubscribe: "model:3" }));
  });
});

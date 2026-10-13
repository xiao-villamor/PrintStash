/**
 * A grid of Models refreshes when a thumbnail it is showing a placeholder for
 * lands, so a fresh upload's preview appears without a reload.
 *
 * Only Models still missing a thumbnail are followed, and no more than
 * `MAX_FOLLOWED` of them: the server caps subscriptions per socket, and a
 * card that already has its preview has nothing to wait for. A thumbnail that
 * merely started, or a different kind, is not an arrival.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearLogin, storeLogin } from "@/lib/auth-store";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import {
  ARRIVAL_COALESCE_MS,
  MAX_FOLLOWED,
  useThumbnailArrivals,
} from "@/lib/use-thumbnail-arrivals";

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();

  subscriptions(): string[] {
    return this.send.mock.calls
      .map(([data]) => data)
      .filter((data) => data.includes("subscribe") && !data.includes("unsubscribe"));
  }
}

let socket: FakeSocket;

function card(id: number, thumbnail: string | null = null) {
  return { id, thumbnail_url: thumbnail };
}

async function opened(): Promise<void> {
  await waitFor(() => expect(socket.onmessage).not.toBeNull());
  socket.onopen?.();
}

/** Wait out the coalescing window, so an arrival that was coming has come. */
async function settled(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ARRIVAL_COALESCE_MS + 50));
}

function deliver(frame: { type: string; model_id?: number; kind?: string; state?: string }) {
  socket.onmessage?.({ data: JSON.stringify({ file_id: 1, ...frame }) });
}

beforeEach(() => {
  storeLogin("", { id: 7, username: "maker", email: null, is_superuser: false });
  socket = new FakeSocket();
  setEventSocketFactory(async () => socket);
});

afterEach(() => clearLogin());

describe("useThumbnailArrivals", () => {
  it("follows only the Models still waiting for a thumbnail", async () => {
    renderHook(() =>
      useThumbnailArrivals([card(1), card(2, "/api/v1/files/9/thumbnail")], vi.fn()),
    );
    await opened();

    expect(socket.subscriptions()).toEqual([JSON.stringify({ subscribe: "model:1" })]);
  });

  it("follows no more than the server allows", async () => {
    const many = Array.from({ length: MAX_FOLLOWED + 5 }, (_, index) => card(index + 1));
    renderHook(() => useThumbnailArrivals(many, vi.fn()));
    await opened();

    expect(socket.subscriptions()).toHaveLength(MAX_FOLLOWED);
  });

  it("opens no socket when every card has its preview", async () => {
    const factory = vi.fn<() => Promise<EventSocket>>(async () => socket);
    setEventSocketFactory(factory);

    renderHook(() => useThumbnailArrivals([card(1, "/api/v1/files/1/thumbnail")], vi.fn()));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(factory).not.toHaveBeenCalled();
  });

  it.each(["ready", "skipped"])("refreshes when a thumbnail is %s", async (state) => {
    const onArrival = vi.fn<() => void>();
    renderHook(() => useThumbnailArrivals([card(1)], onArrival));
    await opened();

    deliver({ type: "derivative", model_id: 1, kind: "thumbnail", state });

    await waitFor(() => expect(onArrival).toHaveBeenCalledTimes(1));
  });

  it.each([
    { label: "a thumbnail that only started", kind: "thumbnail", state: "running" },
    { label: "another kind", kind: "metadata", state: "ready" },
  ])("ignores $label", async ({ kind, state }) => {
    const onArrival = vi.fn<() => void>();
    renderHook(() => useThumbnailArrivals([card(1)], onArrival));
    await opened();

    deliver({ type: "derivative", model_id: 1, kind, state });

    await settled();
    expect(onArrival).not.toHaveBeenCalled();
  });

  it("refreshes after a reconnect", async () => {
    const onArrival = vi.fn<() => void>();
    renderHook(() => useThumbnailArrivals([card(1)], onArrival));
    await opened();

    deliver({ type: "resync" });

    await waitFor(() => expect(onArrival).toHaveBeenCalled());
  });

  it("refreshes once the server confirms it follows the Model", async () => {
    // A thumbnail that landed between the list read and the subscription was
    // announced to nobody; the confirmation is the cue to look again.
    const onArrival = vi.fn<() => void>();
    renderHook(() => useThumbnailArrivals([card(1)], onArrival));
    await opened();

    socket.onmessage?.({ data: JSON.stringify({ type: "subscribed", channel: "model:1" }) });

    await waitFor(() => expect(onArrival).toHaveBeenCalledTimes(1));
  });

  it("refreshes a page of placeholders once for its burst of confirmations", async () => {
    // Every followed Model is acknowledged; a refresh per acknowledgement
    // would refetch every list query once per placeholder on the page.
    const onArrival = vi.fn<() => void>();
    renderHook(() => useThumbnailArrivals([card(1), card(2), card(3)], onArrival));
    await opened();

    for (const id of [1, 2, 3]) {
      socket.onmessage?.({ data: JSON.stringify({ type: "subscribed", channel: `model:${id}` }) });
    }

    await settled();
    expect(onArrival).toHaveBeenCalledTimes(1);
  });

  it("ignores the confirmation for a Model it does not show", async () => {
    const onArrival = vi.fn<() => void>();
    renderHook(() => useThumbnailArrivals([card(1)], onArrival));
    await opened();

    socket.onmessage?.({ data: JSON.stringify({ type: "subscribed", channel: "model:2" }) });

    await settled();
    expect(onArrival).not.toHaveBeenCalled();
  });

  it("does not resubscribe when the same page re-renders", async () => {
    const { rerender } = renderHook(({ models }) => useThumbnailArrivals(models, vi.fn()), {
      initialProps: { models: [card(1)] },
    });
    await opened();

    rerender({ models: [card(1)] });

    expect(socket.subscriptions()).toHaveLength(1);
  });
});

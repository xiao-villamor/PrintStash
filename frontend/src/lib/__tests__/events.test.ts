/*
 * The events socket every view shares to hear that background work changed.
 *
 * One connection per tab, opened while anything listens and closed when
 * nothing does: a socket per component would multiply server load by the
 * number of mounted views. It reconnects with a bounded backoff, and after a
 * reconnect re-follows every Model a view still shows, because the server
 * forgot the old connection's subscriptions. A frame it does not understand is
 * ignored rather than guessed at; a newer server may send kinds this build
 * predates.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearLogin, storeLogin } from "@/lib/auth-store";
import type { EventNotice, EventSocket } from "@/lib/events";

type Events = typeof import("@/lib/events");

class FakeSocket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn<() => void>();

  open(): void {
    this.onopen?.();
  }

  frame(data: string): void {
    this.onmessage?.({ data });
  }

  drop(): void {
    this.onclose?.();
  }

  sent(): string[] {
    return this.send.mock.calls.map(([data]) => data);
  }
}

let events: Events;
let stops: (() => void)[] = [];

function subscribe(listener: (notice: EventNotice) => void): () => void {
  const stop = events.subscribeEvents(listener);
  stops.push(stop);
  return stop;
}

function follow(modelId: number, listener: (notice: EventNotice) => void): () => void {
  const stop = events.followModel(modelId, listener);
  stops.push(stop);
  return stop;
}

let sockets: FakeSocket[];
const opened = vi.fn<() => Promise<EventSocket>>();

beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  stops = [];
  storeLogin("", { id: 7, username: "maker", email: null, is_superuser: false });
  sockets = [];
  opened.mockReset();
  opened.mockImplementation(async () => {
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket;
  });
  events = await import("@/lib/events");
  events.setEventSocketFactory(opened);
});

afterEach(() => {
  for (const stop of stops) stop();
  vi.clearAllTimers();
  vi.useRealTimers();
});

/** Let the factory's promise resolve and the socket open. */
async function connected(): Promise<FakeSocket> {
  await vi.advanceTimersByTimeAsync(0);
  const socket = sockets.at(-1)!;
  socket.open();
  return socket;
}

describe("subscribeEvents", () => {
  it("delivers each notice the server sends", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    subscribe(heard);
    const socket = await connected();

    socket.frame(
      JSON.stringify({
        type: "job",
        job_id: "j1",
        kind: "ingestion.upload",
        state: "running",
        progress: 40,
      }),
    );

    expect(heard).toHaveBeenCalledWith({
      type: "job",
      job_id: "j1",
      kind: "ingestion.upload",
      state: "running",
      progress: 40,
    });
  });

  it("shares one connection between listeners", async () => {
    subscribe(vi.fn<(notice: EventNotice) => void>());
    subscribe(vi.fn<(notice: EventNotice) => void>());
    await connected();

    expect(opened).toHaveBeenCalledTimes(1);
  });

  it("closes the connection when the last listener leaves", async () => {
    const first = subscribe(vi.fn<(notice: EventNotice) => void>());
    const second = subscribe(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();

    first();
    expect(socket.close).not.toHaveBeenCalled();
    second();

    expect(socket.close).toHaveBeenCalledTimes(1);
  });

  it.each([
    { label: "an unknown kind", frame: JSON.stringify({ type: "hologram" }) },
    { label: "a frame that is not JSON", frame: "not json" },
  ])("ignores $label", async ({ frame }) => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    subscribe(heard);
    const socket = await connected();

    socket.frame(frame);

    expect(heard).not.toHaveBeenCalled();
  });

  it("reconnects after the connection drops", async () => {
    subscribe(vi.fn<(notice: EventNotice) => void>());
    const first = await connected();

    first.drop();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(opened).toHaveBeenCalledTimes(2);
  });

  it("backs off while the server stays unreachable", async () => {
    opened.mockRejectedValue(new Error("offline"));
    subscribe(vi.fn<(notice: EventNotice) => void>());
    await vi.advanceTimersByTimeAsync(0);
    expect(opened).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(opened).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1_999);
    expect(opened).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);

    expect(opened).toHaveBeenCalledTimes(3);
  });

  it("closes a connection that opens after the last listener left", async () => {
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    stop();

    await vi.advanceTimersByTimeAsync(0);

    expect(sockets).toHaveLength(1);
    expect(sockets[0].close).toHaveBeenCalledTimes(1);
  });

  it("stops reconnecting once nobody listens", async () => {
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();
    socket.drop();

    stop();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(opened).toHaveBeenCalledTimes(1);
  });
});

describe("followModel", () => {
  it("subscribes to the Model's channel once the socket is open", async () => {
    follow(3, vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();

    expect(socket.sent()).toEqual([JSON.stringify({ subscribe: "model:3" })]);
  });

  it("hears only its own Model's derivatives", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    follow(3, heard);
    const socket = await connected();

    socket.frame(
      JSON.stringify({
        type: "derivative",
        model_id: 4,
        file_id: 1,
        kind: "thumbnail",
        state: "ready",
      }),
    );
    socket.frame(
      JSON.stringify({
        type: "derivative",
        model_id: 3,
        file_id: 2,
        kind: "thumbnail",
        state: "ready",
      }),
    );

    expect(heard).toHaveBeenCalledTimes(1);
    expect(heard).toHaveBeenCalledWith(expect.objectContaining({ model_id: 3, file_id: 2 }));
  });

  it("hears a resync", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    follow(3, heard);
    const socket = await connected();

    socket.frame(JSON.stringify({ type: "resync" }));

    expect(heard).toHaveBeenCalledWith({ type: "resync" });
  });

  it("follows the Model again on a new connection", async () => {
    follow(3, vi.fn<(notice: EventNotice) => void>());
    const first = await connected();
    first.drop();
    await vi.advanceTimersByTimeAsync(1_000);

    const second = sockets.at(-1)!;
    second.open();

    expect(second.sent()).toEqual([JSON.stringify({ subscribe: "model:3" })]);
  });

  it("unsubscribes when the last view of the Model leaves", async () => {
    const first = follow(3, vi.fn<(notice: EventNotice) => void>());
    const second = follow(3, vi.fn<(notice: EventNotice) => void>());
    subscribe(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();

    first();
    expect(socket.sent()).not.toContain(JSON.stringify({ unsubscribe: "model:3" }));
    second();

    expect(socket.sent()).toContain(JSON.stringify({ unsubscribe: "model:3" }));
  });
});

describe("the default connection", () => {
  class FakeWebSocket {
    static last: FakeWebSocket | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: { data: unknown }) => void) | null = null;
    send = vi.fn<(data: string) => void>();
    close = vi.fn<() => void>();

    constructor(readonly url: string) {
      FakeWebSocket.last = this;
    }
  }

  beforeEach(async () => {
    vi.resetModules();
    FakeWebSocket.last = null;
    // The ticket comes from the real API client over a stubbed network.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ ticket: "t/1", expires_in: 30 }), {
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    vi.stubGlobal("WebSocket", FakeWebSocket);
    events = await import("@/lib/events");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("cancels the ticket when its last subscriber leaves", async () => {
    const ticket = Promise.withResolvers<Response>();
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      () => ticket.promise,
    );
    vi.stubGlobal("fetch", fetch);
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    stop();
    expect(fetch.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    ticket.resolve(new Response(JSON.stringify({ ticket: "old", expires_in: 30 })));
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeWebSocket.last).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("relays frames from a ticketed events socket", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    subscribe(heard);
    follow(3, vi.fn<(notice: EventNotice) => void>());
    await vi.advanceTimersByTimeAsync(0);
    const ws = FakeWebSocket.last!;

    ws.onopen?.();
    ws.onmessage?.({ data: JSON.stringify({ type: "resync" }) });

    expect(ws.url).toMatch(/\/api\/v1\/events\/ws\?ticket=t%2F1$/);
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ subscribe: "model:3" }));
    expect(heard).toHaveBeenCalledWith({ type: "resync" });
  });

  it("closes the underlying socket when nobody listens", async () => {
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    await vi.advanceTimersByTimeAsync(0);
    const ws = FakeWebSocket.last!;
    ws.onopen?.();

    stop();

    expect(ws.close).toHaveBeenCalledTimes(1);
  });

  it("reconnects when the underlying socket closes", async () => {
    subscribe(vi.fn<(notice: EventNotice) => void>());
    await vi.advanceTimersByTimeAsync(0);
    const first = FakeWebSocket.last!;
    first.onopen?.();

    first.onclose?.();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(FakeWebSocket.last).not.toBe(first);
  });
});

/** Disposing a connection invalidates its pending factory, handlers and reconnect timer. */
describe("event connection lifetimes", () => {
  it("closes an abandoned connection after listeners return", async () => {
    const obsolete = Promise.withResolvers<EventSocket>();
    opened.mockReturnValueOnce(obsolete.promise);
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    stop();
    const heard = vi.fn<(notice: EventNotice) => void>();
    subscribe(heard);
    const old = new FakeSocket();
    obsolete.resolve(old);
    await vi.advanceTimersByTimeAsync(0);
    expect(old.close).toHaveBeenCalledOnce();
    expect(sockets).toHaveLength(1);
    sockets[0].open();
    sockets[0].frame('{"type":"resync"}');
    expect(heard).toHaveBeenCalledWith({ type: "resync" });
  });

  it("ignores callbacks from a disposed event connection", async () => {
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    const old = await connected();
    const oldMessage = old.onmessage;
    const oldClose = old.onclose;
    stop();
    const heard = vi.fn<(notice: EventNotice) => void>();
    subscribe(heard);
    await connected();
    oldMessage?.({ data: '{"type":"resync"}' });
    oldClose?.();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(heard).not.toHaveBeenCalled();
    expect(opened).toHaveBeenCalledTimes(2);
  });

  it("does not retry a failed abandoned factory", async () => {
    const obsolete = Promise.withResolvers<EventSocket>();
    opened.mockReturnValueOnce(obsolete.promise);
    const stop = subscribe(vi.fn<(notice: EventNotice) => void>());
    stop();
    const fresh = subscribe(vi.fn<(notice: EventNotice) => void>());
    await connected();
    obsolete.reject(new Error("offline"));
    await vi.advanceTimersByTimeAsync(0);
    fresh();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(opened).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops a notice delivery when a listener retires the session", async () => {
    subscribe(() => clearLogin());
    const heard = vi.fn<(notice: EventNotice) => void>();
    subscribe(heard);
    const socket = await connected();
    socket.frame('{"type":"resync"}');
    expect(heard).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledOnce();
  });

  it("retires the active event socket on logout", async () => {
    subscribe(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();
    clearLogin();
    expect(socket.close).toHaveBeenCalledOnce();
    socket.drop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(opened).toHaveBeenCalledOnce();
  });

  it("reauthorizes event channels after login", async () => {
    const heard = vi.fn<(notice: EventNotice) => void>();
    follow(3, heard);
    const first = await connected();
    clearLogin();
    storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false });
    const current = await connected();
    expect(current).not.toBe(first);
    expect(current.sent()).toEqual(['{"subscribe":"model:3"}']);
    current.frame('{"type":"resync"}');
    expect(heard).toHaveBeenCalledWith({ type: "resync" });
  });

  it("keeps Model follow cleanup idempotent", async () => {
    const first = follow(3, vi.fn<(notice: EventNotice) => void>());
    follow(3, vi.fn<(notice: EventNotice) => void>());
    subscribe(vi.fn<(notice: EventNotice) => void>());
    const socket = await connected();
    first();
    first();
    expect(socket.sent()).not.toContain('{"unsubscribe":"model:3"}');
  });
});

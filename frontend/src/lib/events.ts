"use client";

/**
 * The events socket: one shared connection per tab that tells the UI when
 * background work changed, so views refetch instead of polling blind.
 *
 * A notice carries ids and a hint, never data a reader needs authorization for;
 * a listener refetches through the authorized endpoints. Delivery is best
 * effort: a notice can be dropped, and after any reconnect the server sends
 * `resync`, which means "refetch whatever you show".
 *
 * Channels are implicit for the user's own Jobs (and, for administrators, every
 * Job); a view that shows a Model follows `model:<id>` to hear about its
 * derivatives, and the server acknowledges each channel it accepts with
 * `subscribed`.
 */

import { createEventsTicket } from "@/lib/api/work";
import { getWsUrl } from "@/lib/api/request";
import type { DerivativeState, JobState } from "@/types";

export type EventNotice =
  | { type: "resync" }
  | { type: "derivative_policy" }
  | { type: "subscribed"; channel: string }
  | { type: "job"; job_id: string; kind: string; state: JobState; progress: number | null }
  | {
      type: "derivative";
      model_id: number;
      file_id: number;
      kind: string;
      state: DerivativeState;
    };

export type EventListener = (notice: EventNotice) => void;

/** The subset of `WebSocket` the client uses, so tests can drive a fake. */
export interface EventSocket {
  onopen: (() => void) | null;
  onclose: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  send(data: string): void;
  close(): void;
}

export type EventSocketFactory = () => Promise<EventSocket>;

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

async function openEventSocket(): Promise<EventSocket> {
  const { ticket } = await createEventsTicket();
  const ws = new WebSocket(getWsUrl(`/api/v1/events/ws?ticket=${encodeURIComponent(ticket)}`));
  const adapter: EventSocket = {
    onopen: null,
    onclose: null,
    onmessage: null,
    send: (data) => ws.send(data),
    close: () => ws.close(),
  };
  ws.onopen = () => adapter.onopen?.();
  ws.onclose = () => adapter.onclose?.();
  // The server only sends text frames.
  ws.onmessage = (event) => adapter.onmessage?.({ data: String(event.data) });
  return adapter;
}

let socketFactory: EventSocketFactory = openEventSocket;
const listeners = new Set<EventListener>();
const followed = new Map<string, number>();
let socket: EventSocket | null = null;
let open = false;
let connecting = false;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let failures = 0;

/** Point the client at a different socket (tests); resets any live connection. */
export function setEventSocketFactory(factory: EventSocketFactory): void {
  disconnect();
  socketFactory = factory;
  failures = 0;
}

function isBrowser(): boolean {
  return "window" in globalThis;
}

function deliver(notice: EventNotice): void {
  for (const listener of listeners) listener(notice);
}

function parse(data: string): EventNotice | null {
  try {
    // The server writes every frame from `app/modules/work/events.py`; a frame
    // with an unknown type (a newer server) is ignored rather than guessed at.
    const notice: EventNotice = JSON.parse(data);
    return notice.type === "resync" ||
      notice.type === "derivative_policy" ||
      notice.type === "subscribed" ||
      notice.type === "job" ||
      notice.type === "derivative"
      ? notice
      : null;
  } catch {
    return null;
  }
}

function send(message: { subscribe: string } | { unsubscribe: string }): void {
  if (socket && open) socket.send(JSON.stringify(message));
}

function scheduleReconnect(): void {
  if (reconnectTimer !== null || listeners.size === 0) return;
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** failures);
  failures += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connect();
  }, delay);
}

async function connect(): Promise<void> {
  if (socket || connecting || listeners.size === 0 || !isBrowser()) return;
  connecting = true;
  let next: EventSocket;
  try {
    next = await socketFactory();
  } catch {
    connecting = false;
    scheduleReconnect();
    return;
  }
  connecting = false;
  if (listeners.size === 0) {
    next.close();
    return;
  }
  socket = next;
  next.onopen = () => {
    open = true;
    failures = 0;
    for (const channel of followed.keys()) send({ subscribe: channel });
  };
  next.onmessage = (event) => {
    const notice = parse(event.data);
    if (notice) deliver(notice);
  };
  next.onclose = () => {
    if (socket !== next) return;
    socket = null;
    open = false;
    scheduleReconnect();
  };
}

function disconnect(): void {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  const current = socket;
  socket = null;
  open = false;
  current?.close();
}

/** Hear every notice this tab receives; the socket lives while anyone listens. */
export function subscribeEvents(listener: EventListener): () => void {
  if (!isBrowser()) return () => {};
  listeners.add(listener);
  void connect();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) disconnect();
  };
}

/**
 * Hear about one Model's derivatives (a placeholder becoming a thumbnail),
 * every `resync`, and the server's `subscribed` acknowledgement of this
 * Model's channel: a derivative that settled before the subscription took
 * effect was announced to nobody, so a follower refetches on that
 * acknowledgement. The server checks the viewer may see the Model.
 */
export function followModel(modelId: number, listener: EventListener): () => void {
  const channel = `model:${modelId}`;
  const filtered: EventListener = (notice) => {
    if (
      notice.type === "resync" ||
      notice.type === "derivative_policy" ||
      (notice.type === "subscribed" && notice.channel === channel) ||
      (notice.type === "derivative" && notice.model_id === modelId)
    )
      listener(notice);
  };
  const unsubscribe = subscribeEvents(filtered);
  const count = followed.get(channel) ?? 0;
  followed.set(channel, count + 1);
  if (count === 0) send({ subscribe: channel });
  return () => {
    unsubscribe();
    const remaining = (followed.get(channel) ?? 1) - 1;
    if (remaining > 0) {
      followed.set(channel, remaining);
      return;
    }
    followed.delete(channel);
    send({ unsubscribe: channel });
  };
}

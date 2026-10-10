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
import { isLoggedIn, onAuthChange } from "@/lib/auth-store";
import { withSessionRequest } from "@/lib/session-transport";
import { getWsUrl } from "@/lib/api/request";
import type { DerivativeState, JobState } from "@/types";

export type EventNotice =
  | { type: "resync" }
  | { type: "derivative_policy" }
  | { type: "subscribed"; channel: string }
  | {
      type: "job";
      job_id: string;
      kind: string;
      state: JobState;
      progress: number | null;
      task_visible: boolean;
    }
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

export type EventSocketFactory = (signal: AbortSignal) => Promise<EventSocket>;

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

async function openEventSocket(signal: AbortSignal): Promise<EventSocket> {
  let closeOpened = () => {};
  try {
    return await withSessionRequest(async (request) => {
      const { ticket } = await createEventsTicket(request.signal);
      request.assertCurrent();
      const ws = new WebSocket(getWsUrl(`/api/v1/events/ws?ticket=${encodeURIComponent(ticket)}`));
      closeOpened = () => ws.close();
      const adapter: EventSocket = {
        onopen: null,
        onclose: null,
        onmessage: null,
        send: (data) => ws.send(data),
        close: () => {
          ws.onopen = null;
          ws.onclose = null;
          ws.onmessage = null;
          ws.close();
        },
      };
      ws.onopen = () => adapter.onopen?.();
      ws.onclose = () => adapter.onclose?.();
      // The server only sends text frames.
      ws.onmessage = (event) => adapter.onmessage?.({ data: String(event.data) });
      return adapter;
    }, signal);
  } catch (error) {
    closeOpened();
    throw error;
  }
}

let socketFactory: EventSocketFactory = openEventSocket;
const listeners = new Set<EventListener>();
const followed = new Map<string, number>();
let socket: EventSocket | null = null;
let open = false;
let connecting = false;
let generation = 0;
let ticketController: AbortController | null = null;
let stopAuth: (() => void) | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let failures = 0;

/** Point the client at a different socket (tests); resets any live connection. */
export function setEventSocketFactory(factory: EventSocketFactory): void {
  disconnect();
  socketFactory = factory;
  failures = 0;
  void connect();
}

function isBrowser(): boolean {
  return "window" in globalThis;
}

function deliver(notice: EventNotice, current: () => boolean): void {
  for (const listener of listeners) {
    if (!current()) return;
    listener(notice);
  }
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
  if (reconnectTimer !== null || listeners.size === 0 || !isLoggedIn()) return;
  const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** failures);
  failures += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connect();
  }, delay);
}

async function connect(): Promise<void> {
  if (socket || connecting || listeners.size === 0 || !isBrowser() || !isLoggedIn()) return;
  const version = generation;
  const controller = new AbortController();
  ticketController = controller;
  connecting = true;
  let next: EventSocket;
  try {
    next = await socketFactory(controller.signal);
  } catch {
    if (version !== generation) return;
    ticketController = null;
    connecting = false;
    scheduleReconnect();
    return;
  }
  if (version !== generation) {
    next.close();
    return;
  }
  ticketController = null;
  connecting = false;
  socket = next;
  const current = () => version === generation && socket === next;
  next.onopen = () => {
    if (!current()) return;
    open = true;
    failures = 0;
    for (const channel of followed.keys()) send({ subscribe: channel });
  };
  next.onmessage = (event) => {
    if (!current()) return;
    const notice = parse(event.data);
    if (notice) deliver(notice, current);
  };
  next.onclose = () => {
    if (!current()) return;
    next.onopen = null;
    next.onclose = null;
    next.onmessage = null;
    socket = null;
    open = false;
    scheduleReconnect();
  };
}

function disconnect(): void {
  generation += 1;
  ticketController?.abort();
  ticketController = null;
  connecting = false;
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  const current = socket;
  socket = null;
  open = false;
  if (current) {
    current.onopen = null;
    current.onclose = null;
    current.onmessage = null;
    current.close();
  }
}

/** Hear every notice this tab receives; the socket lives while anyone listens. */
export function subscribeEvents(listener: EventListener): () => void {
  if (!isBrowser()) return () => {};
  listeners.add(listener);
  stopAuth ??= onAuthChange(() => {
    disconnect();
    failures = 0;
    void connect();
  });
  void connect();
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    listeners.delete(listener);
    if (listeners.size === 0) {
      disconnect();
      stopAuth?.();
      stopAuth = null;
    }
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
  let active = true;
  return () => {
    if (!active) return;
    active = false;
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

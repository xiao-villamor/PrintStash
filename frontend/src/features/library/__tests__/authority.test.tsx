/** Revision lookups preserve ordinary lists and retire private presentation on access changes. */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { focusManager, onlineManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLibraryAuthority } from "../authority";
import { libraryBrowseKeys } from "../browse";
import { acquireAssetUrl, peekCachedAssetUrl } from "@/lib/asset-cache";
import { getJson } from "@/lib/api/request";
import { clearLogin, getUser, isLoggedIn } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { setEventSocketFactory, type EventSocket } from "@/lib/events";
import { aModelListItem } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { LibraryBrowsePage } from "@/types/library-browse";

class Socket implements EventSocket {
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send() {}
  close() {}
  resync() {
    this.onmessage?.({ data: '{"type":"resync"}' });
  }
}

const page: LibraryBrowsePage = {
  items: [{ kind: "model", model: aModelListItem({ name: "Retained private model" }) }],
  next_cursor: null,
  total: 1,
  browse_revision: "r1",
  authorization_revision: "a1",
};
const params = { view: "all", sort: "name-asc", limit: 24 } as const;
const revisionPath = "GET /api/v1/models/browse/revision";
const same = { browse_revision: "r1", authorization_revision: "a1" };
let socket: Socket;

function Probe({
  presentation = page,
  enabled = true,
  eventsReady = true,
  refresh = () => {},
  retired = () => {},
}: {
  presentation?: LibraryBrowsePage | null;
  enabled?: boolean;
  eventsReady?: boolean;
  refresh?: () => void;
  retired?: () => void;
}) {
  const state = useLibraryAuthority(presentation, {
    enabled,
    eventsReady,
    onRefresh: refresh,
    onAuthorityRetired: retired,
  });
  return (
    <>
      {!state.authorizationChanged && presentation && <p>Retained private model</p>}
      {state.refreshRequired && <p>Refresh required</p>}
      {state.error && <p>Revision unavailable</p>}
      <button onClick={() => void state.refresh()}>Refresh</button>
      <output>{state.checking ? "Checking" : "Settled"}</output>
    </>
  );
}

beforeEach(() => {
  focusManager.setFocused(true);
  onlineManager.setOnline(true);
  socket = new Socket();
  setEventSocketFactory(async () => socket);
});
afterEach(() => {
  cleanup();
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function settled() {
  await screen.findByText("Settled");
}

describe("useLibraryAuthority", () => {
  it("checks permissions immediately while deferring the first events connection", async () => {
    const opened = vi.fn<() => Promise<EventSocket>>(async () => socket);
    setEventSocketFactory(opened);
    const app = renderApp(<Probe eventsReady={false} />, {
      routes: { [revisionPath]: json(same) },
    });
    await settled();
    expect(app.requests().filter((x) => x.url.includes("/revision"))).toHaveLength(1);
    expect(opened).not.toHaveBeenCalled();
    app.rerender(<Probe />);
    await waitFor(() => expect(opened).toHaveBeenCalledTimes(1));
    app.rerender(<Probe eventsReady={false} />);
    act(() => socket.resync());
    await settled();
    expect(app.requests().filter((x) => x.url.includes("/revision"))).toHaveLength(2);
    expect(opened).toHaveBeenCalledTimes(1);
  });

  it("waits for the first presentation before connecting events", async () => {
    const opened = vi.fn<() => Promise<EventSocket>>(async () => socket);
    setEventSocketFactory(opened);
    const app = renderApp(<Probe presentation={null} />, {
      routes: { [revisionPath]: json(same) },
    });
    await settled();
    expect(opened).not.toHaveBeenCalled();
    app.rerender(<Probe />);
    await settled();
    expect(opened).toHaveBeenCalledTimes(1);
  });

  it("keeps the events connection across a pending destination", async () => {
    const opened = vi.fn<() => Promise<EventSocket>>(async () => socket);
    setEventSocketFactory(opened);
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await settled();
    expect(opened).toHaveBeenCalledTimes(1);
    app.rerender(<Probe presentation={null} />);
    act(() => socket.resync());
    await settled();
    expect(app.requests().filter((x) => x.url.includes("/revision"))).toHaveLength(1);
    app.rerender(<Probe presentation={{ ...page }} />);
    await settled();
    expect(opened).toHaveBeenCalledTimes(1);
    expect(app.requests().filter((x) => x.url.includes("/revision"))).toHaveLength(2);
  });

  it("checks authority after mounting a cached page", async () => {
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await settled();
    expect(app.requests().filter((x) => x.url.includes("/revision"))).toHaveLength(1);
    expect(screen.getByText("Retained private model")).toBeVisible();
  });

  it("preserves a list when its browse revision changes", async () => {
    renderApp(<Probe />, { routes: { [revisionPath]: json({ ...same, browse_revision: "r2" }) } });
    expect(await screen.findByText("Refresh required")).toBeVisible();
    expect(screen.getByText("Retained private model")).toBeVisible();
  });

  it("retires private scope when authorization changes", async () => {
    const retired = vi.fn<() => void>(() => {
      expect(isLoggedIn()).toBe(true);
      expect(screen.queryByText("Retained private model")).toBeNull();
    });
    const version = getSessionVersion();
    const app = renderApp(<Probe retired={retired} />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: { [revisionPath]: json({ ...same, authorization_revision: "a2" }) },
    });
    await waitFor(() => expect(screen.queryByText("Retained private model")).toBeNull());
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params))).toBeUndefined();
    expect(getSessionVersion()).toBeGreaterThan(version);
    expect(getUser()?.id).toBe(1);
    expect(retired).toHaveBeenCalledOnce();
  });

  it("ignores cached authority from a previous mount", async () => {
    const pending = Promise.withResolvers<Response>();
    const retired = vi.fn<() => void>();
    const app = renderApp(<Probe retired={retired} />, {
      seed: [
        [
          libraryBrowseKeys.authority,
          {
            authority: { ...same, authorization_revision: "obsolete" },
            requestSequence: 0,
            receiptSequence: 0,
            sessionVersion: getSessionVersion(),
          },
        ],
      ],
      routes: { [revisionPath]: () => pending.promise },
    });
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    expect(screen.getByText("Retained private model")).toBeVisible();
    expect(retired).not.toHaveBeenCalled();
    await act(async () => pending.resolve(json(same)));
    await settled();
    expect(retired).not.toHaveBeenCalled();
  });

  it("rejects a probe started before a newer page", async () => {
    const old = Promise.withResolvers<Response>();
    const current = Promise.withResolvers<Response>();
    const retired = vi.fn<() => void>();
    const signals: (AbortSignal | null | undefined)[] = [];
    const app = renderApp(<Probe retired={retired} />, {
      routes: {
        [revisionPath]: (_url, init) => {
          signals.push(init?.signal);
          return signals.length === 1 ? old.promise : current.promise;
        },
      },
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    app.rerender(
      <Probe presentation={{ ...page, authorization_revision: "a2" }} retired={retired} />,
    );
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0]?.aborted).toBe(true);
    await act(async () => old.resolve(json({ ...same, authorization_revision: "obsolete" })));
    expect(retired).not.toHaveBeenCalled();
    await act(async () => current.resolve(json({ ...same, authorization_revision: "a2" })));
    await settled();
    expect(screen.getByText("Retained private model")).toBeVisible();
    expect(retired).not.toHaveBeenCalled();
  });

  it("rechecks an identical page receipt", async () => {
    const old = Promise.withResolvers<Response>();
    const current = Promise.withResolvers<Response>();
    const signals: (AbortSignal | null | undefined)[] = [];
    const app = renderApp(<Probe />, {
      seed: [[libraryBrowseKeys.pages(params), { pages: [page], pageParams: [null] }]],
      routes: {
        [revisionPath]: (_url, init) => {
          signals.push(init?.signal);
          return signals.length === 1 ? old.promise : current.promise;
        },
      },
    });
    await waitFor(() => expect(signals).toHaveLength(1));
    const retained = app.client.getQueryData(libraryBrowseKeys.pages(params));
    act(() =>
      app.client.setQueryData(libraryBrowseKeys.pages(params), {
        pages: [structuredClone(page)],
        pageParams: [null],
      }),
    );
    expect(app.client.getQueryData(libraryBrowseKeys.pages(params))).toBe(retained);
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0]?.aborted).toBe(true);
    await act(async () => old.resolve(json({ ...same, authorization_revision: "obsolete" })));
    expect(screen.getByText("Retained private model")).toBeVisible();
    await act(async () => current.resolve(json(same)));
    await settled();
    expect(screen.queryByText("Refresh required")).toBeNull();
  });

  it("keeps authority successes outside browse receipts", async () => {
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await settled();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(app.requests()).toHaveLength(1);
  });

  it("coalesces resync while a probe is pending", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, { routes: { [revisionPath]: () => pending.promise } });
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    socket.onopen?.();
    act(() => {
      socket.resync();
      socket.resync();
    });
    expect(app.requests()).toHaveLength(1);
    await act(async () => pending.resolve(json(same)));
    await settled();
  });

  it("checks authority after focus returns", async () => {
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await settled();
    act(() => focusManager.setFocused(false));
    act(() => focusManager.setFocused(true));
    await waitFor(() => expect(app.requests()).toHaveLength(2));
  });

  it("checks authority after reconnect", async () => {
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await settled();
    act(() => onlineManager.setOnline(false));
    act(() => onlineManager.setOnline(true));
    await waitFor(() => expect(app.requests()).toHaveLength(2));
  });

  it("polls only while foregrounded", async () => {
    vi.useFakeTimers();
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByText("Settled")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(app.requests()).toHaveLength(2);
    act(() => focusManager.setFocused(false));
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(app.requests()).toHaveLength(2);
  });

  it("cancels a probe when its last view unmounts", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderApp(<Probe />, {
      routes: {
        [revisionPath]: (_url, init) => {
          signal = init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(json(same)));
  });

  it("rejects a late retired session response", async () => {
    const pending = Promise.withResolvers<Response>();
    const retired = vi.fn<() => void>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Probe retired={retired} />, {
      routes: {
        [revisionPath]: (_url, init) => {
          signal = init?.signal;
          return pending.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    act(() => clearLogin());
    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(json({ ...same, authorization_revision: "obsolete" })));
    expect(retired).not.toHaveBeenCalled();
    expect(screen.queryByText("Refresh required")).toBeNull();
  });

  it("retains private output when revision lookup fails", async () => {
    renderApp(<Probe />, { routes: { [revisionPath]: json({ detail: "unavailable" }, 503) } });
    expect(await screen.findByText("Revision unavailable")).toBeVisible();
    expect(screen.getByText("Retained private model")).toBeVisible();
  });

  it("delegates explicit refresh to the browse owner", async () => {
    const refresh = vi.fn<() => void>();
    const app = renderApp(<Probe refresh={refresh} />, {
      routes: { [revisionPath]: json({ ...same, browse_revision: "r2" }) },
    });
    await screen.findByText("Refresh required");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(refresh).toHaveBeenCalledOnce();
    expect(app.requests()).toHaveLength(1);
  });

  it("rechecks the same cached page on remount", async () => {
    const next = Promise.withResolvers<Response>();
    const app = renderApp(<Probe />, {
      routes: { [revisionPath]: json({ ...same, browse_revision: "r2" }) },
    });
    await screen.findByText("Refresh required");
    app.rerender(<></>);
    app.route({ [revisionPath]: () => next.promise });
    app.rerender(<Probe />);
    await waitFor(() => expect(app.requests()).toHaveLength(2));
    expect(screen.queryByText("Refresh required")).toBeNull();
    await act(async () => next.resolve(json(same)));
    await settled();
    expect(screen.queryByText("Refresh required")).toBeNull();
  });

  it("checks authority after a settled resync", async () => {
    const app = renderApp(<Probe />, { routes: { [revisionPath]: json(same) } });
    await settled();
    socket.onopen?.();
    act(() => socket.resync());
    await waitFor(() => expect(app.requests()).toHaveLength(2));
  });

  it("preserves pending list reads on ordinary revision change", async () => {
    const authority = Promise.withResolvers<Response>();
    const list = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Probe />, {
      routes: {
        [revisionPath]: () => authority.promise,
        "GET /private/list": (_url, init) => {
          signal = init?.signal;
          return list.promise;
        },
      },
    });
    const read = getJson<string[]>("/private/list");
    await act(async () => authority.resolve(json({ ...same, browse_revision: "r2" })));
    await screen.findByText("Refresh required");
    expect(signal?.aborted).toBe(false);
    await act(async () => list.resolve(json(["Still authorized"])));
    expect(await read).toEqual(["Still authorized"]);
  });

  it("retires pending private reads on authorization change", async () => {
    const authority = Promise.withResolvers<Response>();
    const list = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Probe />, {
      routes: {
        [revisionPath]: () => authority.promise,
        "GET /private/list": (_url, init) => {
          signal = init?.signal;
          return list.promise;
        },
      },
    });
    const read = getJson<string[]>("/private/list").catch((error: Error) => error);
    await act(async () => authority.resolve(json({ ...same, authorization_revision: "a2" })));
    await waitFor(() => expect(signal?.aborted).toBe(true));
    await act(async () => list.resolve(json(["Must disappear"])));
    expect(await read).toMatchObject({ name: "AbortError" });
  });

  it("revokes mounted asset leases on authorization change", async () => {
    const revoked = vi.fn<(url: string) => void>();
    vi.stubGlobal("URL", { createObjectURL: () => "blob:private", revokeObjectURL: revoked });
    const authority = Promise.withResolvers<Response>();
    renderApp(<Probe />, {
      routes: {
        [revisionPath]: () => authority.promise,
        "GET /private/thumbnail": new Response("image bytes"),
      },
    });
    const lease = acquireAssetUrl("/private/thumbnail");
    expect(await lease.url).toBe("blob:private");
    await act(async () => authority.resolve(json({ ...same, authorization_revision: "a2" })));
    await waitFor(() => expect(peekCachedAssetUrl("/private/thumbnail")).toBeNull());
    expect(revoked).toHaveBeenCalledWith("blob:private");
    lease.release();
  });

  it.each([
    { label: "missing authorization", payload: { browse_revision: "r1" } },
    { label: "null authority", payload: null },
    { label: "non-string revision", payload: { ...same, browse_revision: 1 } },
    { label: "empty token", payload: { ...same, authorization_revision: "" } },
  ])("retains private output for malformed authority responses ($label)", async ({ payload }) => {
    const retired = vi.fn<() => void>();
    renderApp(<Probe retired={retired} />, { routes: { [revisionPath]: json(payload) } });
    expect(await screen.findByText("Revision unavailable")).toBeVisible();
    expect(screen.getByText("Retained private model")).toBeVisible();
    expect(retired).not.toHaveBeenCalled();
  });

  it("shares authority requests between mounted views", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderApp(
      <>
        <Probe />
        <Probe />
      </>,
      { routes: { [revisionPath]: () => pending.promise } },
    );
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    await act(async () => pending.resolve(json(same)));
    await waitFor(() => expect(screen.getAllByText("Settled")).toHaveLength(2));
    expect(app.requests()).toHaveLength(1);
  });

  it.each([
    { label: "null page", presentation: null, enabled: true },
    { label: "disabled hook", presentation: page, enabled: false },
  ])("skips checks without an active presentation ($label)", async ({ presentation, enabled }) => {
    const app = renderApp(<Probe presentation={presentation} enabled={enabled} />);
    await settled();
    expect(app.requests()).toHaveLength(0);
  });
});

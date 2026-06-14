import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getUrl,
  getWsUrl,
  invalidateApiCache,
} from "@/lib/api/request";
import { getJson, sendJson, sendAction } from "@/lib/api/request";

/**
 * request.ts keeps a small in-memory GET cache (30s TTL) with in-flight
 * deduplication, sitting *underneath* TanStack Query. These tests pin its real
 * behaviour: cache hits skip the network, concurrent calls share one request,
 * `fresh` bypasses the cache, and any mutation clears it.
 */

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  // Start each test with an empty cache (also drops any prior inflight map).
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getUrl / getWsUrl", () => {
  it("returns the path unchanged in the browser (same-origin proxy)", () => {
    expect(getUrl("/api/v1/models")).toBe("/api/v1/models");
  });

  it("derives a ws/wss URL from the current location", () => {
    const url = getWsUrl("/api/v1/printers/3/ws");
    expect(url).toMatch(/^wss?:\/\/.+\/api\/v1\/printers\/3\/ws$/);
  });
});

describe("getJson caching", () => {
  it("serves a second call from cache without a second fetch", async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }]));

    const first = await getJson("/api/v1/models");
    const second = await getJson("/api/v1/models");

    expect(first).toEqual([{ id: 1 }]);
    expect(second).toEqual([{ id: 1 }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("deduplicates concurrent in-flight requests for the same path", async () => {
    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValue(
      new Promise<Response>((r) => {
        resolve = r;
      }),
    );

    const both = Promise.all([
      getJson("/api/v1/tags"),
      getJson("/api/v1/tags"),
    ]);
    resolve(jsonResponse([{ id: 9 }]));
    const [a, b] = await both;

    expect(a).toEqual([{ id: 9 }]);
    expect(b).toEqual([{ id: 9 }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("bypasses the cache when { fresh: true } is passed", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await getJson("/api/v1/printers", { fresh: true });
    await getJson("/api/v1/printers", { fresh: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    // fresh reads must not be cached or served to non-fresh reads either.
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("refetches after invalidateApiCache clears the cache", async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }]));

    await getJson("/api/v1/models");
    invalidateApiCache("/api/v1/models");
    await getJson("/api/v1/models");

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("auth headers", () => {
  it("attaches a bearer token from storage when present", async () => {
    window.localStorage.setItem("printstash.token", "abc123");
    fetchMock.mockResolvedValue(jsonResponse([]));

    await getJson("/api/v1/models", { fresh: true });

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer abc123");
  });

  it("omits the Authorization header when there is no token", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await getJson("/api/v1/models", { fresh: true });

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });
});

describe("mutations", () => {
  it("sendJson issues the right method/body and clears the GET cache", async () => {
    // Prime the cache, then mutate and confirm a follow-up GET refetches.
    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }]));
    await getJson("/api/v1/collections");

    fetchMock.mockResolvedValue(jsonResponse({ id: 2, name: "New" }));
    const created = await sendJson("/api/v1/collections", "POST", { name: "New" });
    expect(created).toEqual({ id: 2, name: "New" });

    const postCall = fetchMock.mock.calls.at(-1)!;
    expect(postCall[1]).toMatchObject({ method: "POST" });
    expect(postCall[1].body).toBe(JSON.stringify({ name: "New" }));

    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }, { id: 2 }]));
    await getJson("/api/v1/collections");
    // 1 initial GET + 1 POST + 1 refetched GET = 3 (cache was busted).
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("sendAction sends a bare method and resolves void on 204", async () => {
    fetchMock.mockResolvedValue(jsonResponse(null, 204));
    await expect(
      sendAction("/api/v1/tags/5", "DELETE"),
    ).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
  });
});

/*
 * The single client every request in the app goes through.
 *
 * Four things live here and each fails in its own way.
 *
 * **URL derivation.** The browser talks to a same-origin proxy, so paths pass
 * through unchanged and only the WebSocket URL is derived — getting the scheme
 * wrong (`ws` on an `https` page) is a mixed-content block, which surfaces as a
 * live view that silently never connects.
 *
 * **Download filenames.** `Content-Disposition` arrives in three spellings
 * (extended `filename*`, quoted, bare) and the value came from a user's upload,
 * so it is attacker-shaped: a name with a path separator or a newline in it is
 * what this sanitises before it reaches the download attribute.
 *
 * **The GET cache.** Caching is what keeps a navigation from refetching the same
 * config four times, and the risk is entirely staleness: an in-flight
 * deduplication that outlives its usefulness, or a cache that survives a
 * mutation, both show the user data they just changed away. So `fresh: true` and
 * `invalidateApiCache` are asserted as hard bypasses, and every mutation clears.
 *
 * **Auth headers.** No token is read out of legacy browser storage, and no empty
 * `Authorization` header is sent — the second would look like a malformed
 * credential to the backend rather than like an anonymous request.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadAuthenticatedFile,
  getJson,
  getUrl,
  getWsUrl,
  invalidateApiCache,
  parseContentDispositionFilename,
  sanitizeDownloadFilename,
  sendAction,
  sendJson,
} from "@/lib/api/request";

/**
 * request.ts keeps a small in-memory GET cache (30s TTL) with in-flight
 * deduplication, sitting *underneath* TanStack Query. These tests pin its real
 * behaviour: cache hits skip the network, concurrent calls share one request,
 * `fresh` bypasses the cache, and any mutation clears it.
 */

/** Any payload the API can serialise as a JSON response body. */
type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

/** A real Response, so `ok`/`status`/`json()`/`text()` behave exactly as in the browser. */
function jsonResponse(data: JsonValue, status = 200): Response {
  const bodyless = status === 204 || status === 205 || status === 304;
  return new Response(bodyless ? null : JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

/** A response body can only be read once, so every call gets a fresh Response. */
function respondWith(data: JsonValue, status = 200): void {
  fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(data, status)));
}

/** The request init of the nth fetch call, which request.ts always supplies. */
function initOf(callIndex: number): RequestInit {
  return fetchMock.mock.calls[callIndex][1] ?? {};
}

function blobResponse(headers: HeadersInit = {}): Response {
  return new Response(new Blob(["payload"]), { status: 200, headers });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  // Start each test with an empty cache (also drops any prior inflight map).
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("getUrl", () => {
  it("returns the path unchanged in the browser (same-origin proxy)", () => {
    expect(getUrl("/api/v1/models")).toBe("/api/v1/models");
  });

  it("derives a ws/wss URL from the current location", () => {
    const url = getWsUrl("/api/v1/printers/3/ws");
    expect(url).toMatch(/^wss?:\/\/.+\/api\/v1\/printers\/3\/ws$/);
  });
});

describe("downloadFilename", () => {
  it("parses and sanitizes extended, quoted, and plain Content-Disposition names", () => {
    expect(
      parseContentDispositionFilename("attachment; filename*=UTF-8''%E2%9C%93%20benchy.gcode"),
    ).toBe("✓ benchy.gcode");
    expect(parseContentDispositionFilename('attachment; filename="/tmp/benchy.gcode"')).toBe(
      "benchy.gcode",
    );
    expect(parseContentDispositionFilename("attachment; filename=..\\evil\\benchy.gcode")).toBe(
      "benchy.gcode",
    );
    expect(
      parseContentDispositionFilename(
        `attachment; filename="unsafe${String.fromCharCode(13, 10)}name.gcode"`,
      ),
    ).toBe("unsafename.gcode");
    expect(
      parseContentDispositionFilename(
        "attachment; filename=\"fallback.gcode\"; filename*=UTF-8''authoritative.gcode",
      ),
    ).toBe("authoritative.gcode");
    expect(
      parseContentDispositionFilename(
        "attachment; filename=\"fallback.gcode\"; filename*=ISO-8859-1''caf%E9.gcode",
      ),
    ).toBe("fallback.gcode");
    expect(
      parseContentDispositionFilename(
        "attachment; filename=\"fallback.gcode\"; filename*=UTF-8''broken%ZZ.gcode",
      ),
    ).toBe("fallback.gcode");
    expect(sanitizeDownloadFilename("C:\\temp\\explicit.gcode")).toBe("explicit.gcode");
    expect(sanitizeDownloadFilename("../")).toBeNull();
  });

  it("uses Content-Disposition when no explicit name is supplied and preserves explicit names", async () => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn<typeof URL.createObjectURL>().mockReturnValue("blob:test"),
      revokeObjectURL: vi.fn<typeof URL.revokeObjectURL>(),
    });
    let clickedFilename = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clickedFilename = this.download;
      },
    );

    fetchMock.mockResolvedValue(
      blobResponse({ "Content-Disposition": 'attachment; filename="project.3mf"' }),
    );
    await downloadAuthenticatedFile("/api/v1/files/7/download");
    expect(clickedFilename).toBe("project.3mf");

    fetchMock.mockResolvedValue(
      blobResponse({ "Content-Disposition": 'attachment; filename="header.gcode"' }),
    );
    await downloadAuthenticatedFile("/api/v1/files/7/download", "explicit.gcode");
    expect(clickedFilename).toBe("explicit.gcode");

    fetchMock.mockResolvedValue(
      blobResponse({ "Content-Disposition": 'attachment; filename="header.gcode"' }),
    );
    await downloadAuthenticatedFile("/api/v1/files/7/download", "../");
    expect(clickedFilename).toBe("header.gcode");

    fetchMock.mockResolvedValue(blobResponse());
    await downloadAuthenticatedFile("/api/v1/files/7/download");
    expect(clickedFilename).toBe("download");
  });
});

describe("getJson", () => {
  it("serves a second call from cache without a second fetch", async () => {
    respondWith([{ id: 1 }]);

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

    const both = Promise.all([getJson("/api/v1/tags"), getJson("/api/v1/tags")]);
    resolve(jsonResponse([{ id: 9 }]));
    const [a, b] = await both;

    expect(a).toEqual([{ id: 9 }]);
    expect(b).toEqual([{ id: 9 }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("bypasses the cache when { fresh: true } is passed", async () => {
    respondWith([]);

    await getJson("/api/v1/printers", { fresh: true });
    await getJson("/api/v1/printers", { fresh: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    // fresh reads must not be cached or served to non-fresh reads either.
    expect(initOf(0)).toMatchObject({ cache: "no-store" });
  });

  it("refetches after invalidateApiCache clears the cache", async () => {
    respondWith([{ id: 1 }]);

    await getJson("/api/v1/models");
    invalidateApiCache("/api/v1/models");
    await getJson("/api/v1/models");

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("authHeaders", () => {
  it("does not attach a browser-readable token from legacy storage", async () => {
    window.localStorage.setItem("printstash.token", "abc123");
    respondWith([]);

    await getJson("/api/v1/models", { fresh: true });

    const headers = new Headers(initOf(0).headers);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("omits the Authorization header when there is no token", async () => {
    respondWith([]);

    await getJson("/api/v1/models", { fresh: true });

    const headers = new Headers(initOf(0).headers);
    expect(headers.get("Authorization")).toBeNull();
  });
});

describe("sendJson", () => {
  it("sendJson issues the right method/body and clears the GET cache", async () => {
    // Prime the cache, then mutate and confirm a follow-up GET refetches.
    respondWith([{ id: 1 }]);
    await getJson("/api/v1/collections");

    respondWith({ id: 2, name: "New" });
    const created = await sendJson("/api/v1/collections", "POST", { name: "New" });
    expect(created).toEqual({ id: 2, name: "New" });

    const postInit = initOf(fetchMock.mock.calls.length - 1);
    expect(postInit).toMatchObject({ method: "POST" });
    expect(postInit.body).toBe(JSON.stringify({ name: "New" }));

    respondWith([{ id: 1 }, { id: 2 }]);
    await getJson("/api/v1/collections");
    // 1 initial GET + 1 POST + 1 refetched GET = 3 (cache was busted).
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("sendAction sends a bare method and resolves void on 204", async () => {
    respondWith(null, 204);
    await expect(sendAction("/api/v1/tags/5", "DELETE")).resolves.toBeUndefined();
    expect(initOf(0)).toMatchObject({ method: "DELETE" });
  });
});

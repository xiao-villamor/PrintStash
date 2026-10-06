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
 * **Session transport.** Query owns JSON freshness. The HTTP layer reads every
 * time and fences headers, bodies and acknowledged writes against the session
 * incarnation that started them. Caller cancellation retains its reason.
 *
 * **Auth headers.** No token is read out of legacy browser storage, and no empty
 * `Authorization` header is sent — the second would look like a malformed
 * credential to the backend rather than like an anonymous request.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadAuthenticatedFile,
  getJson,
  getAuthenticatedBlob,
  getAuthenticatedText,
  getDerivedText,
  getUrl,
  getWsUrl,
  invalidateApiCache,
  parseContentDispositionFilename,
  sanitizeDownloadFilename,
  sendAction,
  sendFormWithProgress,
  sendForm,
  sendJson,
} from "@/lib/api/request";
import { queryClient, queryKeys } from "@/lib/query-client";
import { FetchBackedXhr } from "@/test-support/fetch-backed-xhr";
import { clearLogin, getUser, storeLogin } from "@/lib/auth-store";

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
  // Clear Query state independently of transport (which does not cache).
  queryClient.clear();
  clearLogin();
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("sendFormWithProgress", () => {
  beforeEach(() => {
    FetchBackedXhr.requests = [];
    vi.stubGlobal("XMLHttpRequest", FetchBackedXhr);
  });

  it("reports transferred bytes while the POST is awaiting its response", async () => {
    let finishRequest: ((response: Response) => void) | undefined;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finishRequest = resolve;
        }),
    );
    const progress = vi.fn<(loaded: number, total: number) => void>();
    const form = new FormData();
    form.append("file", new File(["archive"], "parts.zip"));
    const pending = sendFormWithProgress<{ job_id: string }>(
      "/api/v1/ingest/archive/inspect",
      form,
      new AbortController().signal,
      progress,
    );

    const request = FetchBackedXhr.requests[0];
    expect(request.method).toBe("POST");
    expect(request.url).toBe("/api/v1/ingest/archive/inspect");
    expect(request.body).toBe(form);
    request.emitProgress(4, 8);
    expect(progress).toHaveBeenCalledWith(4, 8);

    finishRequest?.(jsonResponse({ job_id: "archive-1" }, 202));
    await expect(pending).resolves.toEqual({ job_id: "archive-1" });
  });

  it("preserves the server's coded upload rejection", async () => {
    respondWith({ detail: "upload_too_large" }, 413);

    await expect(
      sendFormWithProgress(
        "/api/v1/ingest/archive/inspect",
        new FormData(),
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toMatchObject({ status: 413, code: "upload_too_large" });
  });

  it("aborts the pending browser transfer", async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    const controller = new AbortController();
    const pending = sendFormWithProgress(
      "/api/v1/ingest/archive/inspect",
      new FormData(),
      controller.signal,
      () => {},
    );

    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("reports a broken connection as a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(
      sendFormWithProgress(
        "/api/v1/ingest/archive/inspect",
        new FormData(),
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toThrow("Failed to fetch");
  });
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
  it("reads current data after another tab changes the session", async () => {
    respondWith([{ name: "old owner" }]);
    await getJson("/api/v1/tags");
    window.dispatchEvent(new StorageEvent("storage", { key: "printstash.user" }));
    respondWith([{ name: "new owner" }]);
    expect(await getJson("/api/v1/tags")).toEqual([{ name: "new owner" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["cached", "fresh", "abortable"] as const)(
    "ignores an old session's 401 on a %s read",
    async (mode) => {
      const pending = Promise.withResolvers<Response>();
      fetchMock.mockReturnValueOnce(pending.promise);
      storeLogin("", { id: 7, username: "old-owner", email: null, is_superuser: false });
      const options =
        mode === "fresh"
          ? { fresh: true }
          : mode === "abortable"
            ? { signal: new AbortController().signal }
            : undefined;
      const oldRead = getJson("/api/v1/auth/me", options);
      const outcome = oldRead.catch((error: Error) => error);
      storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false });
      pending.resolve(jsonResponse({ detail: "expired" }, 401));
      expect(await outcome).toMatchObject({
        name: "AbortError",
        message: "request_session_changed",
      });
      expect(getUser()?.id).toBe(9);
    },
  );

  it("does not reuse a response that preceded explicit invalidation", async () => {
    const previous = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(previous.promise);
    const oldRead = getJson("/api/v1/tags");
    invalidateApiCache();
    previous.resolve(jsonResponse([{ name: "old value" }]));
    await oldRead;
    respondWith([{ name: "new value" }]);
    expect(await getJson("/api/v1/tags")).toEqual([{ name: "new value" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("returns fresh JSON on every transport read", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ id: 1 }))
      .mockResolvedValueOnce(jsonResponse({ id: 2 }));
    expect(await getJson("/api/v1/models")).toEqual({ id: 1 });
    expect(await getJson("/api/v1/models")).toEqual({ id: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps concurrent transport reads independent", async () => {
    const first = Promise.withResolvers<Response>();
    const second = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const reads = [getJson("/api/v1/models"), getJson("/api/v1/models")];
    second.resolve(jsonResponse({ id: 2 }));
    first.resolve(jsonResponse({ id: 1 }));
    expect(await Promise.all(reads)).toEqual([{ id: 1 }, { id: 2 }]);
  });

  it("accepts the legacy fresh option on uncached reads", async () => {
    respondWith([]);

    await getJson("/api/v1/printers", { fresh: true });
    await getJson("/api/v1/printers", { fresh: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    // Compatibility options cannot reinstate a transport cache.
    expect(initOf(0)).toMatchObject({ cache: "no-store" });
  });

  it("reads the server after the compatibility invalidation bridge", async () => {
    respondWith([{ id: 1 }]);

    await getJson("/api/v1/models");
    invalidateApiCache("/api/v1/models");
    await getJson("/api/v1/models");

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("protected byte session isolation", () => {
  it("ignores an old session's unauthorized thumbnail response", async () => {
    const pending = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(pending.promise);
    storeLogin("", { id: 7, username: "old-owner", email: null, is_superuser: false });
    const oldRead = getAuthenticatedBlob("/api/v1/files/1/thumbnail");
    const outcome = oldRead.catch((error: Error) => error);
    storeLogin("", { id: 9, username: "new-owner", email: null, is_superuser: false });
    pending.resolve(jsonResponse({ detail: "expired" }, 401));
    expect(await outcome).toMatchObject({ name: "AbortError", message: "request_session_changed" });
    expect(getUser()?.id).toBe(9);
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
  it("sends the requested JSON method and payload", async () => {
    // Reads remain network requests around an acknowledged mutation.
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
    // One initial GET, one POST and one current GET.
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("sendAction sends a bare method and resolves void on 204", async () => {
    respondWith(null, 204);
    await expect(sendAction("/api/v1/tags/5", "DELETE")).resolves.toBeUndefined();
    expect(initOf(0)).toMatchObject({ method: "DELETE" });
  });

  it("reads current server state after a failed mutation", async () => {
    respondWith([{ id: 1 }]);
    await getJson("/api/v1/models");

    respondWith({ detail: "multipart_model_member_not_found" }, 400);
    await expect(
      sendJson("/api/v1/multipart-models/7", "PUT", { parts: [] }),
    ).rejects.toMatchObject({ status: 400 });

    respondWith([{ id: 2 }]);
    expect(await getJson("/api/v1/models")).toEqual([{ id: 2 }]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("getAuthenticatedBlob", () => {
  it("retries a failed browser delivery through the API", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    fetchMock.mockResolvedValueOnce(new Response("payload"));

    const blob = await getAuthenticatedBlob("/api/v1/files/7/download");

    expect(blob.size).toBe(7);
    expect(new Headers(initOf(1).headers).get("x-printstash-delivery")).toBe("proxy");
  });

  it("does not retry a cancelled read", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));

    await expect(getAuthenticatedBlob("/api/v1/files/7/download")).rejects.toThrow("Aborted");

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry an authorization denial", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "forbidden" }, 403));

    await expect(getAuthenticatedBlob("/api/v1/files/7/download")).rejects.toMatchObject({
      status: 403,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("getDerivedText", () => {
  it("returns the text of a derived resource", async () => {
    fetchMock.mockResolvedValueOnce(new Response("G1 X10"));

    await expect(getDerivedText("/api/v1/files/7/toolpath")).resolves.toEqual({
      ready: true,
      text: "G1 X10",
    });
  });

  it("reports the derivative's state while it is not ready", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ state: "running" }), {
        status: 202,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(getDerivedText("/api/v1/files/7/toolpath")).resolves.toEqual({
      ready: false,
      state: "running",
    });
  });

  it.each([
    { label: "no state", body: {} },
    { label: "a state this build does not know", body: { state: "hologram" } },
  ])("rejects a pending answer with $label", async ({ body }) => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status: 202,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(getDerivedText("/api/v1/files/7/toolpath")).rejects.toThrow(
      "derivative_state_invalid",
    );
  });

  it("rejects a failed derivative with its reason", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "toolpath_invalid_bgcode" }), {
        status: 422,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(getDerivedText("/api/v1/files/7/toolpath")).rejects.toMatchObject({
      status: 422,
      code: "toolpath_invalid_bgcode",
    });
  });
});

describe("getAuthenticatedText", () => {
  it("preserves cancellation while revalidating protected text", async () => {
    fetchMock.mockImplementation(
      (_url, options) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = options?.signal;
          signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    );
    const controller = new AbortController();
    const content = getAuthenticatedText("/api/v1/files/7/download", controller.signal);
    const reason = new DOMException("Navigation", "AbortError");
    controller.abort(reason);
    await expect(content).rejects.toBe(reason);
    expect(initOf(0).cache).toBe("no-cache");
    expect(initOf(0).signal?.aborted).toBe(true);
  });
});

/** Deferred bodies expose races that header-only transport guards cannot catch. */
describe("session request completion", () => {
  function establishSession(id = 7) {
    storeLogin("", { id, username: `owner-${id}`, email: null, is_superuser: false });
  }

  it.each([
    { label: "JSON", read: () => getJson("/api/v1/models") },
    { label: "text", read: () => getAuthenticatedText("/api/v1/files/7/download") },
    { label: "blob", read: () => getAuthenticatedBlob("/api/v1/files/7/download") },
  ])("rejects old-session response bodies for $label", async ({ read }) => {
    establishSession();
    const body = Promise.withResolvers<Uint8Array>();
    const response = new Response(
      new ReadableStream({
        async start(controller) {
          controller.enqueue(await body.promise);
          controller.close();
        },
      }),
    );
    fetchMock.mockResolvedValueOnce(response);
    const pending = read();
    const outcome = pending.catch((error: Error) => error);
    await vi.waitFor(() => expect(response.bodyUsed).toBe(true));
    establishSession(9);
    body.resolve(new TextEncoder().encode('{"id":7}'));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(getUser()?.id).toBe(9);
  });

  it("ignores unauthorized bodies from retired sessions", async () => {
    establishSession();
    const body = Promise.withResolvers<Uint8Array>();
    const response = new Response(
      new ReadableStream({
        async start(controller) {
          controller.enqueue(await body.promise);
          controller.close();
        },
      }),
      { status: 401 },
    );
    fetchMock.mockResolvedValueOnce(response);
    const pending = getJson("/api/v1/auth/me");
    const outcome = pending.catch((error: Error) => error);
    await vi.waitFor(() => expect(response.bodyUsed).toBe(true));
    establishSession(9);
    body.resolve(new TextEncoder().encode('{"detail":"expired"}'));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(getUser()?.id).toBe(9);
  });

  it("preserves current-session unauthorized errors", async () => {
    establishSession();
    respondWith({ detail: "invalid_or_expired_token" }, 401);
    await expect(getJson("/api/v1/auth/me")).rejects.toMatchObject({
      status: 401,
      code: "invalid_or_expired_token",
    });
    expect(getUser()).toBeNull();
  });

  it.each([
    { label: "JSON", write: () => sendJson("/api/v1/models", "POST", {}) },
    { label: "form", write: () => sendForm("/api/v1/ingest", new FormData()) },
    { label: "action", write: () => sendAction("/api/v1/models/7", "DELETE") },
  ])("rejects retired mutation acknowledgements for $label", async ({ write }) => {
    establishSession();
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const pending = write();
    const outcome = pending.catch((error: Error) => error);
    establishSession(9);
    queryClient.setQueryData(queryKeys.models, [{ id: 9 }]);
    headers.resolve(jsonResponse({ id: 7 }));
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(queryClient.getQueryData(queryKeys.models)).toEqual([{ id: 9 }]);
    expect(queryClient.getQueryState(queryKeys.models)?.isInvalidated).toBe(false);
  });

  it("aborts pending transports on logout", async () => {
    establishSession();
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const pending = getJson("/api/v1/models");
    const outcome = pending.catch((error: Error) => error);
    clearLogin();
    expect(initOf(0).signal?.aborted).toBe(true);
    headers.resolve(jsonResponse([]));
    expect(await outcome).toMatchObject({ name: "AbortError" });
  });

  it("rejects same-account session replacement", async () => {
    establishSession();
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const pending = getJson("/api/v1/models");
    const outcome = pending.catch((error: Error) => error);
    establishSession();
    expect(initOf(0).signal?.aborted).toBe(true);
    headers.resolve(jsonResponse([]));
    expect(await outcome).toMatchObject({ name: "AbortError" });
  });

  it("preserves caller cancellation", async () => {
    const headers = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(headers.promise);
    const caller = new AbortController();
    const reason = new DOMException("Navigation cancelled", "AbortError");
    const pending = getJson("/api/v1/models", { signal: caller.signal });
    const outcome = pending.catch((error: Error) => error);
    caller.abort(reason);
    headers.resolve(jsonResponse([]));
    expect(await outcome).toBe(reason);
  });
});

/** Upload progress belongs to the session that started the browser transfer. */
describe("upload session retirement", () => {
  it("aborts retired upload progress", async () => {
    FetchBackedXhr.requests = [];
    vi.stubGlobal("XMLHttpRequest", FetchBackedXhr);
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    const progress = vi.fn<(loaded: number, total: number) => void>();
    const pending = sendFormWithProgress(
      "/api/v1/ingest",
      new FormData(),
      new AbortController().signal,
      progress,
    );
    const outcome = pending.catch((error: Error) => error);
    const transfer = FetchBackedXhr.requests[0];
    transfer.emitProgress(1, 8);
    clearLogin();
    transfer.emitProgress(8, 8);
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(progress.mock.calls).toEqual([[1, 8]]);
  });
});

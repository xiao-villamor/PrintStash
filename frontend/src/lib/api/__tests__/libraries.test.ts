/*
 * The external-library CRUD calls, and the one that must never be cached.
 *
 * A library's scan status changes while the user is watching the page, so the
 * listing is deliberately `fresh` on every call. Serving it from the GET cache
 * would show a scan as still running for as long as the cache lives — the exact
 * screen where a user is waiting for a number to move.
 *
 * The rest pin method and path, because these routes are addressed by id and a
 * `PATCH` sent as a `PUT` (or to the collection rather than the member) is a
 * request the backend answers successfully having changed something else.
 *
 * The feature-flag pair is here rather than with the config tests because the
 * flag is what gates this whole surface: read it wrong and the panel renders for
 * an installation whose operator never enabled external libraries.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createExternalLibrary,
  deleteExternalLibrary,
  enrollExternalLibraryRoot,
  listExternalLibraries,
  scanExternalLibrary,
  updateExternalLibrary,
} from "@/lib/api/libraries";
import { getVaultConfig, updateVaultConfig } from "@/lib/api/config";
import { aVaultConfig } from "@/test-support/factories";
import { json } from "@/test-support/render";

/**
 * Pin the External Libraries (NAS mirroring) API client to the exact wire
 * contract the backend router expects: paths, HTTP verbs, and request bodies.
 * A drift here is a silent break of the whole NAS settings/upload UI.
 */

/** One value inside a JSON body the fake backend below hands back. */
type WireValue =
  | string
  | number
  | boolean
  | null
  | readonly WireValue[]
  | { readonly [key: string]: WireValue };

function jsonResponse(data: WireValue, status = 200): Response {
  // A 204 carries no body at all, and `new Response` rejects one outright.
  const body = status === 204 ? null : JSON.stringify(data);
  return new Response(body, { status, headers: { "content-type": "application/json" } });
}

const fetchMock = vi.fn<typeof fetch>();

/**
 * Answer every fetch with a freshly built response. A `Response` body can only
 * be read once, so a single shared instance would break the repeat-call tests.
 */
function respondWith(data: WireValue, status = 200) {
  fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(data, status)));
}

const library = {
  id: 7,
  edit_epoch: "a".repeat(32),
  edit_version: 1,
  name: "nas-main",
  root_path: "/mnt/nas/models",
  enabled: true,
  scan_interval_minutes: 60,
  collection_mode: "mirror",
  scan_schedule: "0 * * * *",
  watch_mode: "auto",
  fs_kind: "local",
  watch_active: true,
  binding_state: "bound",
  binding_reason: null,
  root_enrollable: false,
  target_collection_id: null,
  last_scanned_at: null,
  last_scan_status: null,
  last_scan_summary: null,
};

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  return { url, init: init! };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();

  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listExternalLibraries", () => {
  it("GETs the libraries collection and bypasses the cache (fresh)", async () => {
    respondWith([library]);

    const result = await listExternalLibraries();

    expect(result).toEqual([library]);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/libraries");
    expect(init).toMatchObject({ cache: "no-store" });
  });

  it("re-fetches on every call rather than serving a stale cache", async () => {
    respondWith([]);

    await listExternalLibraries();
    await listExternalLibraries();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("createExternalLibrary", () => {
  it("POSTs the create body and returns the created library", async () => {
    respondWith(library);

    const body = {
      name: "nas-main",
      root_path: "/mnt/nas/models",
      scan_interval_minutes: 30,
      collection_mode: "mirror" as const,
    };
    const created = await createExternalLibrary(body);

    expect(created).toEqual(library);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/libraries");
    expect(init).toMatchObject({ method: "POST" });
    expect(init.body).toBe(JSON.stringify(body));
  });
});

describe("updateExternalLibrary", () => {
  it("PATCHes the addressed library with a partial body", async () => {
    respondWith({ ...library, enabled: false, edit_version: 2 });

    const updated = await updateExternalLibrary(7, { enabled: false }, { base: library });

    expect(updated.enabled).toBe(false);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/libraries/7");
    expect(init).toMatchObject({ method: "PATCH" });
    expect(init.body).toBe(JSON.stringify({ enabled: false }));
  });
});

describe("enrollExternalLibraryRoot", () => {
  it("POSTs the exact root confirmation", async () => {
    respondWith(library);

    const result = await enrollExternalLibraryRoot(7, {
      confirm_root_path: "/mnt/nas/models",
    });

    expect(result).toEqual(library);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/libraries/7/root/enroll");
    expect(init).toMatchObject({ method: "POST" });
    expect(init.body).toBe(JSON.stringify({ confirm_root_path: "/mnt/nas/models" }));
  });
});

describe("deleteExternalLibrary", () => {
  it("DELETEs the addressed library and resolves void on 204", async () => {
    respondWith(null, 204);

    await expect(deleteExternalLibrary(7)).resolves.toBeUndefined();
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/libraries/7");
    expect(init).toMatchObject({ method: "DELETE" });
  });
});

describe("scanExternalLibrary", () => {
  it("POSTs to the scan endpoint and returns the queued job", async () => {
    respondWith({ job_id: "scan-1", state: "pending", message: "library scan queued" }, 202);

    const resp = await scanExternalLibrary(7);

    expect(resp.job_id).toBe("scan-1");
    expect(resp.state).toBe("pending");
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/libraries/7/scan");
    expect(init).toMatchObject({ method: "POST" });
  });
});

describe("getVaultConfig", () => {
  it("reads external_libraries_enabled from GET /api/v1/config", async () => {
    respondWith({ storage_backend: "local", external_libraries_enabled: true });

    const cfg = await getVaultConfig();

    expect(cfg.external_libraries_enabled).toBe(true);
    expect(lastCall().url).toBe("/api/v1/config");
  });

  it("PUTs a toggle of external_libraries_enabled", async () => {
    fetchMock.mockResolvedValueOnce(
      json(aVaultConfig({ edit_version: 2, external_libraries_enabled: false })),
    );

    const cfg = await updateVaultConfig(
      { external_libraries_enabled: false },
      { base: aVaultConfig() },
    );

    expect(cfg.external_libraries_enabled).toBe(false);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/config");
    expect(init).toMatchObject({ method: "PUT" });
    expect(init.body).toBe(JSON.stringify({ external_libraries_enabled: false }));
  });
});

describe("reader cancellation", () => {
  it("aborts an active external libraries read", async () => {
    const controller = new AbortController();
    let delivered: AbortSignal | null = null;
    fetchMock.mockImplementation(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          const signal = options?.signal;
          if (!signal) throw new Error("Cancellation signal is required");
          delivered = signal;
          signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    );
    const outcome = listExternalLibraries({ signal: controller.signal }).catch(
      (error: Error) => error,
    );
    controller.abort();
    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
    expect(delivered).toMatchObject({ aborted: true });
  });
});

describe("management caller cancellation", () => {
  it.each([
    {
      label: "create",
      call: (signal: AbortSignal) =>
        createExternalLibrary({ name: "Test", root_path: "/mnt/test" }, { signal }),
    },
    {
      label: "update",
      call: (signal: AbortSignal) =>
        updateExternalLibrary(7, { enabled: false }, { signal, base: library }),
    },
    {
      label: "enroll",
      call: (signal: AbortSignal) =>
        enrollExternalLibraryRoot(7, { confirm_root_path: "/mnt/test" }, { signal }),
    },
    { label: "delete", call: (signal: AbortSignal) => deleteExternalLibrary(7, { signal }) },
    { label: "scan", call: (signal: AbortSignal) => scanExternalLibrary(7, { signal }) },
  ])("aborts the exact $label command", async ({ call }) => {
    const controller = new AbortController();
    let delivered: AbortSignal | null | undefined;
    fetchMock.mockImplementation(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          delivered = options?.signal;
          delivered?.addEventListener("abort", () => reject(delivered?.reason), { once: true });
        }),
    );

    const pending = call(controller.signal).catch((error: Error) => error);
    controller.abort();

    await expect(pending).resolves.toMatchObject({ name: "AbortError" });
    expect(delivered).toMatchObject({ aborted: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("Source edit preconditions", () => {
  it("sends the captured source base", async () => {
    respondWith({ ...library, name: "Saved", edit_version: 2 });
    await updateExternalLibrary(7, { name: "Saved" }, { base: library });
    expect(new Headers(lastCall().init.headers).get("If-Match")).toBe(
      `"library-source-7-e${library.edit_epoch}-v1"`,
    );
    expect(new Headers(lastCall().init.headers).get("X-PrintStash-Edit-Contract")).toBe(
      "conditional-v1",
    );
  });
  it.each([
    { label: "identity", change: { id: 8, edit_version: 2 } },
    { label: "history", change: { edit_epoch: "b".repeat(32), edit_version: 2 } },
    { label: "version", change: { edit_version: 1 } },
  ])("rejects an invalid source receipt: $label", async ({ change }) => {
    respondWith({ ...library, ...change });
    await expect(updateExternalLibrary(7, { name: "Saved" }, { base: library })).rejects.toThrow(
      /Invalid .*acknowledgement/,
    );
  });
});

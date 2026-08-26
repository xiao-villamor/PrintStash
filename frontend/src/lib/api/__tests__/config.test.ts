import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getVaultConfig, updateVaultConfig } from "@/lib/api/config";
import { invalidateApiCache } from "@/lib/api/request";

const fetchMock = vi.fn<typeof fetch>();

type WireValue =
  | string
  | number
  | boolean
  | null
  | readonly WireValue[]
  | { readonly [key: string]: WireValue };

function respondWith(data: WireValue): void {
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(data), {
        headers: { "content-type": "application/json" },
      }),
    ),
  );
}

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  return { url, init: init! };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("vault config — external libraries flag", () => {
  it("reads external_libraries_enabled from GET /api/v1/config", async () => {
    respondWith({ storage_backend: "local", external_libraries_enabled: true });

    const cfg = await getVaultConfig();

    expect(cfg.external_libraries_enabled).toBe(true);
    expect(lastCall().url).toBe("/api/v1/config");
  });

  it("PUTs a toggle of external_libraries_enabled", async () => {
    respondWith({ storage_backend: "local", external_libraries_enabled: false });

    const cfg = await updateVaultConfig({ external_libraries_enabled: false });

    expect(cfg.external_libraries_enabled).toBe(false);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/config");
    expect(init).toMatchObject({ method: "PUT" });
    expect(init.body).toBe(JSON.stringify({ external_libraries_enabled: false }));
  });
});

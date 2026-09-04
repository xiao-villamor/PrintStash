/*
 * Talking to a Spoolman instance the operator configured, through our backend.
 *
 * These are thin calls and the tests are correspondingly thin — method, path,
 * and the one query parameter that changes what comes back. `include_archived`
 * is the case worth naming: archived spools are ones the user retired, and
 * showing them in a picker means offering filament that is gone, while omitting
 * them from an inventory view hides history the user asked for. Same endpoint,
 * two intents, one flag.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getSpoolmanStatus,
  listSpools,
  syncSpoolmanFilaments,
  testSpoolman,
  updateSpoolman,
} from "@/lib/api/spoolman";
import { invalidateApiCache } from "@/lib/api/request";

/**
 * Pin the Spoolman API client to the backend router's wire contract: paths,
 * verbs, and bodies. Drift here silently breaks the Spoolman settings card and
 * the spool selectors in the print flows.
 */

function jsonResponse<T>(data: T, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

const status = {
  enabled: true,
  base_url: "http://spoolman.local:7912",
  has_api_key: false,
  write_enabled: true,
  write_force: false,
  connected: true,
  version: "0.18.0",
  error: null,
  native_hook_detected: false,
};

function lastCall() {
  const [input, init] = fetchMock.mock.calls.at(-1)!;
  return { url: String(input), init: init ?? {} };
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

describe("getSpoolmanStatus", () => {
  it("GETs the spoolman status", async () => {
    fetchMock.mockResolvedValue(jsonResponse(status));
    const result = await getSpoolmanStatus();
    expect(result).toEqual(status);
    expect(lastCall().url).toBe("/api/v1/spoolman");
  });
});

describe("updateSpoolman", () => {
  it("PUTs the partial config body", async () => {
    fetchMock.mockResolvedValue(jsonResponse(status));
    const body = { base_url: "http://spoolman.local:7912", enabled: true };
    await updateSpoolman(body);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/spoolman");
    expect(init).toMatchObject({ method: "PUT" });
    expect(init.body).toBe(JSON.stringify(body));
  });
});

describe("testSpoolman", () => {
  it("POSTs to the test endpoint", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        connected: true,
        version: "0.18.0",
        error: null,
        native_hook_detected: false,
      }),
    );
    const res = await testSpoolman();
    expect(res.connected).toBe(true);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/spoolman/test");
    expect(init).toMatchObject({ method: "POST" });
  });
});

describe("listSpools", () => {
  it("GETs the spools inventory", async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }]));
    const result = await listSpools();
    expect(result).toEqual([{ id: 1 }]);
    expect(lastCall().url).toBe("/api/v1/spoolman/spools");
  });

  it("passes include_archived when requested", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));
    await listSpools(true);
    expect(lastCall().url).toBe("/api/v1/spoolman/spools?include_archived=true");
  });
});

describe("syncSpoolmanFilaments", () => {
  it("POSTs to the sync endpoint and returns counts", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ created: 2, updated: 1, adopted: 0, unlinked: 0 }));
    const res = await syncSpoolmanFilaments();
    expect(res.created).toBe(2);
    const { url, init } = lastCall();
    expect(url).toBe("/api/v1/spoolman/sync-filaments");
    expect(init).toMatchObject({ method: "POST" });
  });
});

/*
 * Storage-insight API calls must preserve their administrator-only paths,
 * fresh-read semantics, pagination, and the uncollected-Model sentinel.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cleanupStorageCache,
  cleanupStorageStaging,
  getCollectionStorage,
  getModelStorage,
  getStorageCapacityActivity,
  getStorageCleanupOpportunities,
  getStorageInventory,
  sampleStorageInventory,
} from "@/lib/api/storage-inventory";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  window.localStorage.clear();
});

afterEach(() => vi.unstubAllGlobals());

describe("storage inventory API", () => {
  it("uses fresh reads for inventory activity cleanup previews", async () => {
    await getStorageInventory();
    await getStorageCapacityActivity();
    await getStorageCleanupOpportunities();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/storage/inventory",
      "/api/v1/storage/inventory/activity",
      "/api/v1/storage/inventory/cleanup-opportunities",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toMatchObject({ cache: "no-store" });
    }
  });

  it("posts explicit measurement plus receipt-safe cleanup actions", async () => {
    await sampleStorageInventory();
    await cleanupStorageStaging();
    await cleanupStorageCache();

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/storage/inventory/sample", "POST"],
      ["/api/v1/storage/inventory/cleanup-staging", "POST"],
      ["/api/v1/storage/inventory/cleanup-cache", "POST"],
    ]);
  });

  it("encodes bounded Collection pagination", async () => {
    await getCollectionStorage(20, 10);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/storage/inventory/collections?offset=20&limit=10",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("distinguishes every Model page from the uncollected page", async () => {
    await getModelStorage(7, 10, 5);
    await getModelStorage(null);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/storage/inventory/models?offset=10&limit=5&collection_id=7",
      "/api/v1/storage/inventory/models?offset=0&limit=10",
    ]);
  });
});

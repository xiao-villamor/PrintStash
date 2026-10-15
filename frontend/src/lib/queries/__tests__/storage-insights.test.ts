/**
 * Storage insights take seconds to measure, so a measurement is kept until the
 * library changes (any vault-stats invalidation) or someone asks for a new one;
 * time alone and leaving the screen never re-measure it.
 */
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { queryClient, queryKeys } from "@/lib/query-client";
import { collectionStorageOptions, storageInsightsOptions } from "@/lib/queries/storage-insights";
import { json } from "@/test-support/render";

// Captured before any harness render replaces them with test defaults.
const productionDefaults = queryClient.getDefaultOptions();
const fetchMock = vi.fn<typeof fetch>();

function inventoryReads() {
  return fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/api/v1/storage/inventory"))
    .length;
}

describe("Storage insights cache", () => {
  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (input) =>
      String(input).includes("/collections") ? json([]) : json({}),
    );
    vi.stubGlobal("fetch", fetchMock);
    client = new QueryClient({ defaultOptions: productionDefaults });
  });

  afterEach(() => {
    client.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reuses an hour-old measurement", async () => {
    await client.fetchQuery(storageInsightsOptions());
    vi.advanceTimersByTime(60 * 60_000);

    await client.fetchQuery(storageInsightsOptions());

    expect(inventoryReads()).toBe(1);
  });

  it("keeps a measurement nobody is viewing", async () => {
    await client.fetchQuery(storageInsightsOptions());
    await client.fetchQuery(collectionStorageOptions(0));
    vi.advanceTimersByTime(60 * 60_000);

    expect(client.getQueryData(storageInsightsOptions().queryKey)).toBeDefined();
    expect(client.getQueryData(collectionStorageOptions(0).queryKey)).toBeDefined();
  });

  it("re-measures after a library write invalidates vault stats", async () => {
    await client.fetchQuery(storageInsightsOptions());
    await client.fetchQuery(collectionStorageOptions(0));

    await client.invalidateQueries({ queryKey: queryKeys.vaultStats });
    await client.fetchQuery(storageInsightsOptions());
    await client.fetchQuery(collectionStorageOptions(0));

    expect(inventoryReads()).toBe(2);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/collections")),
    ).toHaveLength(2);
  });
});

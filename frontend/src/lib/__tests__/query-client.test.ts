/** Ingest completion retires stale reads without touching a replacement session. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { queryClient, queryKeys, refreshVaultAfterIngest } from "@/lib/query-client";

import { clearLogin, retirePrivateSessionScope } from "@/lib/auth-store";

import type { QueryKey } from "@tanstack/react-query";

/**
 * The keyed-invalidation map is the heart of the TanStack Query <-> backend
 * cache integration: a mutated API path must bust exactly the query keys it can
 * affect (and no more). These tests pin that mapping so a future regex tweak
 * can't silently stop, say, model writes from refreshing the vault stats.
 */

/** One recorded call to a `{ queryKey }`-filtered query-client method. */
type QueryFilterCall = readonly [filters?: { queryKey?: QueryKey }, ...rest: unknown[]];

/** A query key as one comparable name, so assertions ignore call order. */
function keyName(key: QueryKey): string {
  return key.join("/");
}

function keyNames(keys: readonly QueryKey[]): string[] {
  return keys.map(keyName).sort();
}

function bustedKeys(calls: readonly QueryFilterCall[]): string[] {
  return calls.map(([filters]) => keyName(filters?.queryKey ?? [])).sort();
}

describe("refreshVaultAfterIngest", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
  });

  it.each([
    { name: "session", retire: clearLogin },
    { name: "permission", retire: retirePrivateSessionScope },
  ])(
    "preserves replacement-$name projections after delayed ingest cancellation",
    async ({ retire }) => {
      const cancellation = Promise.withResolvers<void>();
      const cancelQueries = queryClient.cancelQueries.bind(queryClient);
      vi.spyOn(queryClient, "cancelQueries").mockImplementation(async (...args) => {
        await cancelQueries(...args);
        await cancellation.promise;
      });
      const refresh = refreshVaultAfterIngest();
      retire();
      const outlinerKey = [...queryKeys.outliner, "collections"];
      const pages = { pages: [{ items: [] }], pageParams: [null] };
      queryClient.setQueryData(outlinerKey, pages);
      queryClient.setQueryData(queryKeys.models, []);
      cancellation.resolve();
      await refresh;
      expect(queryClient.getQueryData(outlinerKey)).toEqual(pages);
      expect(queryClient.getQueryState(queryKeys.models)?.isInvalidated).toBe(false);
    },
  );

  it("refreshes current-session projections after delayed ingest cancellation", async () => {
    const cancellation = Promise.withResolvers<void>();
    const cancelQueries = queryClient.cancelQueries.bind(queryClient);
    vi.spyOn(queryClient, "cancelQueries").mockImplementation(async (...args) => {
      await cancelQueries(...args);
      await cancellation.promise;
    });
    const outlinerKey = [...queryKeys.outliner, "collections"];
    queryClient.setQueryData(outlinerKey, { pages: [{ items: [] }], pageParams: [null] });
    queryClient.setQueryData(queryKeys.models, []);
    const refresh = refreshVaultAfterIngest();
    cancellation.resolve();
    await refresh;
    expect(queryClient.getQueryData(outlinerKey)).toBeUndefined();
    expect(queryClient.getQueryState(queryKeys.models)?.isInvalidated).toBe(true);
  });

  it("cancels stale upload-time reads, then refreshes grid, tree, and totals", async () => {
    const cancel = vi.spyOn(queryClient, "cancelQueries").mockResolvedValue();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();

    await refreshVaultAfterIngest();

    const vaultKeys = keyNames([
      queryKeys.models,
      queryKeys.collections,
      queryKeys.vaultStats,
      queryKeys.multipartModels,
    ]);
    expect(bustedKeys(cancel.mock.calls)).toEqual(vaultKeys);
    expect(bustedKeys(invalidate.mock.calls)).toEqual(vaultKeys);
    expect(cancel.mock.invocationCallOrder.at(-1)).toBeLessThan(
      invalidate.mock.invocationCallOrder[0],
    );

    cancel.mockRestore();
    invalidate.mockRestore();
  });
});

describe("queryKeys", () => {
  it("derives detail keys as a prefix of the resource key for partial matching", () => {
    expect(queryKeys.model(7)).toEqual(["models", 7]);
    expect(queryKeys.model(7)[0]).toBe(queryKeys.models[0]);
    expect(queryKeys.printer(3)).toEqual(["printers", 3]);
    expect(queryKeys.printer(3)[0]).toBe(queryKeys.printers[0]);
  });

  it("maps filament/printer profile keys to their backend resource roots", () => {
    expect(queryKeys.filamentProfiles).toEqual(["filament-profiles"]);
    expect(queryKeys.printerProfiles).toEqual(["printer-profiles"]);
  });
});

describe("outliner mutation refresh", () => {
  it("discards pages when an ingest finishes", async () => {
    const key = [...queryKeys.outliner, "collections"];
    queryClient.setQueryData(key, { pages: [{ items: [] }], pageParams: [null] });
    await refreshVaultAfterIngest();
    expect(queryClient.getQueryData(key)).toBeUndefined();
    queryClient.clear();
  });
});

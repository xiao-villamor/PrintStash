/*
 * Which caches a write invalidates — the mapping that decides whether the UI
 * agrees with the database after a mutation.
 *
 * Under-invalidating is the failure users report as "I have to refresh". It is
 * almost always a *derived* cache somebody forgot: a model write changes the
 * vault totals and the collection counts, and a collection rename changes every
 * model card that shows a label. So the rows here are mostly about second-order
 * keys rather than the obvious one.
 *
 * The prefix-collision cases are the sharp ones. `/filament-profiles` and
 * `/printer-profiles` both start with a path a naive check would read as
 * `/printers`, so a substring match busts the wrong cache. Their feature command owner
 * publishes and revalidates its catalogs; the transport must leave them alone.
 *
 * An unrecognised path invalidates nothing rather than everything. Blanket
 * invalidation would hide every one of the bugs above.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  invalidateQueriesForPath,
  queryClient,
  queryKeys,
  refreshVaultAfterIngest,
} from "@/lib/query-client";

import { clearLogin, retirePrivateSessionScope } from "@/lib/auth-store";

import type { QueryKey } from "@tanstack/react-query";
import type { MockInstance } from "vitest";

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

describe("invalidateQueriesForPath", () => {
  let spy: MockInstance<typeof queryClient.invalidateQueries>;

  beforeEach(() => {
    spy = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
  });

  afterEach(() => {
    spy.mockRestore();
  });

  it("refreshes AI capability after a generation activation", () => {
    invalidateQueriesForPath("/api/v1/config/ai-search/generations/1/activate");
    expect(bustedKeys(spy.mock.calls)).toEqual(["ai-search"]);
  });

  it("keeps resource estimates from invalidating configuration", () => {
    invalidateQueriesForPath("/api/v1/config/ai-search/generations/estimate");
    expect(bustedKeys(spy.mock.calls)).toEqual([]);
  });

  it("busts collections AND models on a collection write (labels affect lists)", () => {
    invalidateQueriesForPath("/api/v1/collections/5");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([queryKeys.collections, queryKeys.models, queryKeys.multipartModels]),
    );
  });

  it("busts tags AND models on a tag write", () => {
    invalidateQueriesForPath("/api/v1/tags");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([queryKeys.tags, queryKeys.models, queryKeys.multipartModels]),
    );
  });

  it("refreshes taxonomy after a model tag mutation", () => {
    invalidateQueriesForPath("/api/v1/models/batch/tags");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([
        queryKeys.tags,
        queryKeys.models,
        queryKeys.vaultStats,
        queryKeys.collections,
        queryKeys.multipartModels,
      ]),
    );
  });

  it("busts models, vault stats AND collections on a model write (stats + counts derive from models)", () => {
    invalidateQueriesForPath("/api/v1/models/12");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([
        queryKeys.models,
        queryKeys.vaultStats,
        queryKeys.collections,
        queryKeys.multipartModels,
      ]),
    );
  });

  it("treats files/ingest/gcode paths as model writes", () => {
    for (const path of ["/api/v1/files/3", "/api/v1/ingest", "/api/v1/gcode-revision/7"]) {
      spy.mockClear();
      invalidateQueriesForPath(path);
      expect(bustedKeys(spy.mock.calls)).toEqual(
        keyNames([
          queryKeys.models,
          queryKeys.vaultStats,
          queryKeys.collections,
          queryKeys.multipartModels,
        ]),
      );
    }
  });

  it("refreshes every multipart query prefix after collection writes", () => {
    invalidateQueriesForPath("/api/v1/collections/5", "PATCH");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([queryKeys.collections, queryKeys.models, queryKeys.multipartModels]),
    );
  });

  it("refreshes multipart reads for every trash lifecycle route", () => {
    for (const path of ["/api/v1/trash/12", "/api/v1/restore/12", "/api/v1/purge/12"]) {
      spy.mockClear();
      invalidateQueriesForPath(path, "POST");
      expect(bustedKeys(spy.mock.calls)).toEqual(
        keyNames([
          queryKeys.models,
          queryKeys.collections,
          queryKeys.vaultStats,
          queryKeys.multipartModels,
        ]),
      );
    }
  });

  it("does not invalidate anything for a non-mutating GET", () => {
    invalidateQueriesForPath("/api/v1/models/12", "GET");
    invalidateQueriesForPath("/api/v1/multipart-models/4/candidates", "HEAD");
    expect(spy).not.toHaveBeenCalled();
  });

  it("busts printers on a printer write", () => {
    invalidateQueriesForPath("/api/v1/printers/3");
    expect(bustedKeys(spy.mock.calls)).toEqual(keyNames([queryKeys.printers]));
  });

  it("leaves filament catalog refresh to its command owner", () => {
    invalidateQueriesForPath("/api/v1/filament-profiles/9");
    expect(spy).not.toHaveBeenCalled();
  });

  it("does NOT mistake /filament-profiles for a printers write", () => {
    invalidateQueriesForPath("/api/v1/filament-profiles");
    expect(bustedKeys(spy.mock.calls)).not.toContain(keyName(queryKeys.printers));
  });

  it("leaves printer catalog refresh to its command owner", () => {
    invalidateQueriesForPath("/api/v1/printer-profiles/2");
    expect(spy).not.toHaveBeenCalled();
  });

  it("leaves synced filament refresh to profiles while retaining Spoolman reads", () => {
    invalidateQueriesForPath("/api/v1/spoolman/sync-filaments");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([queryKeys.spoolmanStatus, queryKeys.spools]),
    );
  });

  it("busts admin users on an admin user write", () => {
    invalidateQueriesForPath("/api/v1/admin/users/4");
    expect(bustedKeys(spy.mock.calls)).toEqual(keyNames([queryKeys.adminUsers]));
  });

  it("does nothing for an unrecognised path", () => {
    invalidateQueriesForPath("/api/v1/health");
    expect(spy).not.toHaveBeenCalled();
  });
});

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
  it.each([
    "/api/v1/models/1",
    "/api/v1/models/1/star",
    "/api/v1/models/batch/tags",
    "/api/v1/models/batch/move",
    "/api/v1/collections/1",
    "/api/v1/multipart-models/1",
    "/api/v1/files/1",
    "/api/v1/trash",
    "/api/v1/models/1/restore",
    "/api/v1/ingest",
  ])("discards continuation pages after a mutation of %s", (path) => {
    const key = [...queryKeys.outliner, "entries", { collection_id: 1 }];
    queryClient.setQueryData(key, {
      pages: [
        { items: [{ id: 1 }], next_cursor: "old" },
        { items: [{ id: 2 }], next_cursor: null },
      ],
      pageParams: [null, "old"],
    });
    invalidateQueriesForPath(path);
    expect(queryClient.getQueryData(key)).toBeUndefined();
    queryClient.clear();
  });

  it("discards pages when an ingest finishes", async () => {
    const key = [...queryKeys.outliner, "collections"];
    queryClient.setQueryData(key, { pages: [{ items: [] }], pageParams: [null] });
    await refreshVaultAfterIngest();
    expect(queryClient.getQueryData(key)).toBeUndefined();
    queryClient.clear();
  });
});

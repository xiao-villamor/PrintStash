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
 * `/printers`, so a substring match busts the wrong cache and leaves the right
 * one stale — asserted in both directions, because either mistake looks like the
 * mapping working.
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

  it("busts collections AND models on a collection write (labels affect lists)", () => {
    invalidateQueriesForPath("/api/v1/collections/5");
    expect(bustedKeys(spy.mock.calls)).toEqual(keyNames([queryKeys.collections, queryKeys.models]));
  });

  it("busts tags AND models on a tag write", () => {
    invalidateQueriesForPath("/api/v1/tags");
    expect(bustedKeys(spy.mock.calls)).toEqual(keyNames([queryKeys.tags, queryKeys.models]));
  });

  it("busts models, vault stats AND collections on a model write (stats + counts derive from models)", () => {
    invalidateQueriesForPath("/api/v1/models/12");
    expect(bustedKeys(spy.mock.calls)).toEqual(
      keyNames([queryKeys.models, queryKeys.vaultStats, queryKeys.collections]),
    );
  });

  it("treats files/ingest/gcode paths as model writes", () => {
    for (const path of ["/api/v1/files/3", "/api/v1/ingest", "/api/v1/gcode-revision/7"]) {
      spy.mockClear();
      invalidateQueriesForPath(path);
      expect(bustedKeys(spy.mock.calls)).toEqual(
        keyNames([queryKeys.models, queryKeys.vaultStats, queryKeys.collections]),
      );
    }
  });

  it("busts printers on a printer write", () => {
    invalidateQueriesForPath("/api/v1/printers/3");
    expect(bustedKeys(spy.mock.calls)).toEqual(keyNames([queryKeys.printers]));
  });

  it("busts filament profiles on the real /filament-profiles path", () => {
    invalidateQueriesForPath("/api/v1/filament-profiles/9");
    expect(bustedKeys(spy.mock.calls)).toEqual(keyNames([queryKeys.filamentProfiles]));
  });

  it("does NOT mistake /filament-profiles for a printers write", () => {
    invalidateQueriesForPath("/api/v1/filament-profiles");
    expect(bustedKeys(spy.mock.calls)).not.toContain(keyName(queryKeys.printers));
  });

  it("busts printer profiles on /printer-profiles (not the printers key)", () => {
    invalidateQueriesForPath("/api/v1/printer-profiles/2");
    const keys = bustedKeys(spy.mock.calls);
    expect(keys).toContain(keyName(queryKeys.printerProfiles));
    expect(keys).not.toContain(keyName(queryKeys.printers));
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
  it("cancels stale upload-time reads, then refreshes grid, tree, and totals", async () => {
    const cancel = vi.spyOn(queryClient, "cancelQueries").mockResolvedValue();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();

    await refreshVaultAfterIngest();

    const vaultKeys = keyNames([queryKeys.models, queryKeys.collections, queryKeys.vaultStats]);
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

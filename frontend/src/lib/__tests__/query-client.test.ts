import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  invalidateQueriesForPath,
  queryClient,
  queryKeys,
  refreshVaultAfterIngest,
} from "@/lib/query-client";

/**
 * The keyed-invalidation map is the heart of the TanStack Query <-> backend
 * cache integration: a mutated API path must bust exactly the query keys it can
 * affect (and no more). These tests pin that mapping so a future regex tweak
 * can't silently stop, say, model writes from refreshing the vault stats.
 */

type Spy = ReturnType<typeof vi.spyOn>;

function bustedKeys(spy: Spy): unknown[][] {
  return spy.mock.calls.map(
    (call: unknown[]) => (call[0] as { queryKey: unknown[] }).queryKey,
  );
}

function expectBusted(spy: Spy, expected: readonly (readonly unknown[])[]) {
  const actual = bustedKeys(spy);
  expect(actual).toHaveLength(expected.length);
  for (const key of expected) {
    expect(actual).toContainEqual([...key]);
  }
}

describe("invalidateQueriesForPath", () => {
  let spy: Spy;

  beforeEach(() => {
    spy = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
  });

  afterEach(() => {
    spy.mockRestore();
  });

  it("busts collections AND models on a collection write (labels affect lists)", () => {
    invalidateQueriesForPath("/api/v1/collections/5");
    expectBusted(spy, [queryKeys.collections, queryKeys.models]);
  });

  it("busts tags AND models on a tag write", () => {
    invalidateQueriesForPath("/api/v1/tags");
    expectBusted(spy, [queryKeys.tags, queryKeys.models]);
  });

  it("busts models, vault stats AND collections on a model write (stats + counts derive from models)", () => {
    invalidateQueriesForPath("/api/v1/models/12");
    expectBusted(spy, [queryKeys.models, queryKeys.vaultStats, queryKeys.collections]);
  });

  it("treats files/ingest/gcode paths as model writes", () => {
    for (const path of [
      "/api/v1/files/3",
      "/api/v1/ingest",
      "/api/v1/gcode-revision/7",
    ]) {
      spy.mockClear();
      invalidateQueriesForPath(path);
      expectBusted(spy, [queryKeys.models, queryKeys.vaultStats, queryKeys.collections]);
    }
  });

  it("busts printers on a printer write", () => {
    invalidateQueriesForPath("/api/v1/printers/3");
    expectBusted(spy, [queryKeys.printers]);
  });

  it("busts filament profiles on the real /filament-profiles path", () => {
    invalidateQueriesForPath("/api/v1/filament-profiles/9");
    expectBusted(spy, [queryKeys.filamentProfiles]);
  });

  it("does NOT mistake /filament-profiles for a printers write", () => {
    invalidateQueriesForPath("/api/v1/filament-profiles");
    expect(bustedKeys(spy)).not.toContainEqual([...queryKeys.printers]);
  });

  it("busts printer profiles on /printer-profiles (not the printers key)", () => {
    invalidateQueriesForPath("/api/v1/printer-profiles/2");
    const keys = bustedKeys(spy);
    expect(keys).toContainEqual([...queryKeys.printerProfiles]);
    expect(keys).not.toContainEqual([...queryKeys.printers]);
  });

  it("busts admin users on an admin user write", () => {
    invalidateQueriesForPath("/api/v1/admin/users/4");
    expectBusted(spy, [queryKeys.adminUsers]);
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

    expectBusted(cancel, [queryKeys.models, queryKeys.collections, queryKeys.vaultStats]);
    expectBusted(invalidate, [queryKeys.models, queryKeys.collections, queryKeys.vaultStats]);
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

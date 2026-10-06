/*
 * One object URL per thumbnail, for the life of the page.
 *
 * Thumbnails are protected, so they are fetched with a bearer header and turned
 * into blob URLs. The old per-component hook re-fetched on every mount, which
 * meant scrolling a card out and back, paginating, or re-entering a folder paid
 * for the same image again and recreated its object URL — the images visibly
 * "popped" on a grid that is scrolled constantly.
 *
 * So the two properties that matter are: a resolved URL is reused
 * *synchronously*, because an async reuse still flashes empty for a frame; and
 * concurrent requests for the same path collapse into one fetch, because a grid
 * mounts fifty cards at once and several may share a thumbnail.
 *
 * The cache is bounded — every entry holds a decoded image alive — and evicting
 * has to revoke the URL it drops, or the memory the cap exists to bound leaks
 * anyway.
 *
 * A thumbnail can change server-side under a reused file id, which no cache key
 * can see. That is what `invalidateCachedAsset` is for, and it has to revoke the
 * old URL rather than merely forget it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acquireAssetUrl,
  getAssetCacheStats,
  getCachedAssetUrl,
  invalidateCachedAsset,
  peekCachedAssetUrl,
} from "@/lib/asset-cache";

/** Resolve `count` distinct assets, to push the cache past its cap. */
async function fill(prefix: string, count: number) {
  for (let index = 0; index < count; index += 1) {
    await getCachedAssetUrl(`/files/${prefix}-${index}/thumbnail`);
  }
}

let created: string[];
let revoked: string[];

beforeEach(() => {
  created = [];
  revoked = [];
  let next = 0;
  vi.stubGlobal("URL", {
    createObjectURL: (): string => {
      const url = `blob:asset-${(next += 1)}`;
      created.push(url);
      return url;
    },
    revokeObjectURL: (url: string): void => {
      revoked.push(url);
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => new Response("png-bytes", { status: 200 })),
  );
});

afterEach(() => {
  window.dispatchEvent(new Event("printstash:auth-changed"));
  vi.unstubAllGlobals();
});

describe("assetCache", () => {
  it("revokes cached thumbnails when another tab changes the session", async () => {
    const path = "/files/cross-tab/thumbnail";
    const oldUrl = await getCachedAssetUrl(path);
    window.dispatchEvent(new StorageEvent("storage", { key: "printstash.user" }));
    expect(peekCachedAssetUrl(path)).toBeNull();
    expect(revoked).toContain(oldUrl);
  });

  it("revokes cached thumbnails when the session owner changes", async () => {
    const path = "/files/session/thumbnail";
    const oldUrl = await getCachedAssetUrl(path);
    window.dispatchEvent(new Event("printstash:auth-changed"));
    expect(peekCachedAssetUrl(path)).toBeNull();
    expect(revoked).toContain(oldUrl);
    expect(await getCachedAssetUrl(path)).not.toBe(oldUrl);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("rejects old thumbnail bytes without erasing the new session's pending request", async () => {
    const previous = Promise.withResolvers<Response>();
    const current = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(previous.promise).mockReturnValueOnce(current.promise);
    const path = "/files/session-pending/thumbnail";
    const oldRead = getCachedAssetUrl(path);
    const oldOutcome = oldRead.catch((error: Error) => error);
    window.dispatchEvent(new Event("printstash:auth-changed"));
    const newRead = getCachedAssetUrl(path);
    previous.resolve(new Response("previous owner"));
    expect(await oldOutcome).toMatchObject({
      name: "AbortError",
      message: "request_session_changed",
    });
    expect(getCachedAssetUrl(path)).toBe(newRead);
    current.resolve(new Response("current owner"));
    expect(await newRead).toBe(created[0]);
    expect(created).toHaveLength(1);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("rejects thumbnail bytes invalidated during their download", async () => {
    const previous = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(previous.promise);
    const path = "/files/invalidated-pending/thumbnail";
    const oldRead = getCachedAssetUrl(path);
    const oldOutcome = oldRead.catch((error: Error) => error);
    invalidateCachedAsset(path);
    previous.resolve(new Response("old bytes"));
    expect(await oldOutcome).toEqual(new Error("asset_request_invalidated"));
    expect(peekCachedAssetUrl(path)).toBeNull();
    expect(created).toHaveLength(0);
  });

  describe("fetching an asset", () => {
    it("returns an object URL for the blob", async () => {
      const url = await getCachedAssetUrl("/files/1/thumbnail");

      expect(url).toBe(created[0]);
    });

    it("fetches the path it was given", async () => {
      await getCachedAssetUrl("/files/2/thumbnail");

      expect(vi.mocked(fetch).mock.calls[0][0]).toContain("/files/2/thumbnail");
    });

    it("propagates a failure rather than caching it", async () => {
      // A cached rejection would leave a thumbnail permanently broken after one
      // transient 500.
      vi.mocked(fetch).mockResolvedValueOnce(new Response("nope", { status: 500 }));

      await expect(getCachedAssetUrl("/files/3/thumbnail")).rejects.toThrow(/500/);

      vi.mocked(fetch).mockResolvedValueOnce(new Response("png-bytes", { status: 200 }));
      await expect(getCachedAssetUrl("/files/3/thumbnail")).resolves.toBeTruthy();
    });
  });

  describe("reusing what it already has", () => {
    it("answers a second request without fetching again", async () => {
      await getCachedAssetUrl("/files/4/thumbnail");

      await getCachedAssetUrl("/files/4/thumbnail");

      expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    });

    it("answers null for a path it has not resolved", () => {
      // The synchronous peek is what removes the "pop"; it has to be honest
      // about a path it does not hold rather than guess a URL.
      expect(peekCachedAssetUrl("/files/5/thumbnail")).toBeNull();
    });

    it("knows a path it has resolved", async () => {
      const url = await getCachedAssetUrl("/files/6/thumbnail");

      expect(peekCachedAssetUrl("/files/6/thumbnail")).toBe(url);
    });

    it("collapses concurrent requests for the same path into one fetch", async () => {
      // A grid mounts fifty cards at once, and several may share a thumbnail.
      const first = getCachedAssetUrl("/files/7/thumbnail");
      const second = getCachedAssetUrl("/files/7/thumbnail");

      await Promise.all([first, second]);

      expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    });

    it("gives concurrent callers the same URL", async () => {
      const [first, second] = await Promise.all([
        getCachedAssetUrl("/files/8/thumbnail"),
        getCachedAssetUrl("/files/8/thumbnail"),
      ]);

      expect(first).toBe(second);
    });
  });

  describe("invalidating one", () => {
    it("forgets the path", async () => {
      // A re-upload can reuse a file id, which no cache key can see.
      await getCachedAssetUrl("/files/9/thumbnail");

      invalidateCachedAsset("/files/9/thumbnail");

      expect(peekCachedAssetUrl("/files/9/thumbnail")).toBeNull();
    });

    it("revokes the URL it dropped", async () => {
      // Forgetting without revoking leaks the decoded image the URL holds
      // alive.
      const url = await getCachedAssetUrl("/files/10/thumbnail");

      invalidateCachedAsset("/files/10/thumbnail");

      expect(revoked).toContain(url);
    });

    it("re-fetches the path afterwards", async () => {
      await getCachedAssetUrl("/files/11/thumbnail");
      invalidateCachedAsset("/files/11/thumbnail");

      await getCachedAssetUrl("/files/11/thumbnail");

      expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
    });

    it("ignores a path it never held", () => {
      invalidateCachedAsset("/files/999/thumbnail");

      expect(revoked).toHaveLength(0);
    });
  });

  describe("bounding memory", () => {
    it("revokes the oldest entries once the cap is passed", async () => {
      // Every entry holds a decoded image alive; a cap that evicts without
      // revoking leaks exactly what it exists to bound.
      await fill("lru", 405);

      expect(revoked.length).toBeGreaterThan(0);
    });

    it("keeps the most recently used entry", async () => {
      await fill("keep", 405);

      expect(peekCachedAssetUrl("/files/keep-404/thumbnail")).not.toBeNull();
    });
  });
});

describe("asset leases and admission", () => {
  it("limits simultaneous protected image downloads to four", async () => {
    const responses = Array.from({ length: 6 }, () => Promise.withResolvers<Response>());
    let index = 0;
    vi.mocked(fetch).mockImplementation(() => responses[index++].promise);
    const leases = responses.map((_, i) => acquireAssetUrl(`/lease/limit-${i}`));
    expect(fetch).toHaveBeenCalledTimes(4);
    responses[0].resolve(new Response("png"));
    await leases[0].url;
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(5));
    responses.slice(1).forEach((response) => response.resolve(new Response("png")));
    await Promise.all(leases.map((lease) => lease.url));
    leases.forEach((lease) => lease.release());
  });

  it("removes abandoned queued image work", async () => {
    const responses = Array.from({ length: 4 }, () => Promise.withResolvers<Response>());
    let index = 0;
    vi.mocked(fetch).mockImplementation(() => responses[index++].promise);
    const active = responses.map((_, i) => acquireAssetUrl(`/lease/queue-${i}`));
    const queued = acquireAssetUrl("/lease/abandoned");
    const abandoned = queued.url.catch((error: Error) => error);
    queued.release();
    responses.forEach((response) => response.resolve(new Response("png")));
    await Promise.all(active.map((lease) => lease.url));
    expect(await abandoned).toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledTimes(4);
    active.forEach((lease) => lease.release());
  });

  it("aborts an unneeded active download", async () => {
    const response = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(response.promise);
    const lease = acquireAssetUrl("/lease/active");
    const outcome = lease.url.catch((error: Error) => error);
    lease.release();
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
    expect(await outcome).toMatchObject({ name: "AbortError" });
    response.resolve(new Response("discard"));
  });

  it("keeps shared work for the remaining image consumer", async () => {
    const response = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(response.promise);
    const first = acquireAssetUrl("/lease/shared");
    const other = acquireAssetUrl("/lease/shared");
    const outcome = first.url.catch((error: Error) => error);
    first.release();
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
    response.resolve(new Response("shared"));
    expect(await other.url).toBe(created[0]);
    expect(await outcome).toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledOnce();
    other.release();
  });

  it("keeps a mounted image URL under count pressure", async () => {
    const mounted = acquireAssetUrl("/lease/mounted");
    const url = await mounted.url;
    await fill("pressure", 405);
    expect(peekCachedAssetUrl("/lease/mounted")).toBe(url);
    expect(revoked).not.toContain(url);
    mounted.release();
  });

  it("bounds inactive image bytes", async () => {
    vi.mocked(fetch).mockImplementation(async () => new Response(new Uint8Array(8 * 1024 * 1024)));
    const urls: string[] = [];
    for (let i = 0; i < 5; i++) urls.push(await getCachedAssetUrl(`/lease/bytes-${i}`));
    expect(revoked).toContain(urls[0]);
    expect(peekCachedAssetUrl("/lease/bytes-4")).toBe(urls[4]);
  });

  it("observes caller cancellation independently", async () => {
    const response = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValueOnce(response.promise);
    const controller = new AbortController();
    const first = acquireAssetUrl("/lease/caller", controller.signal);
    const other = acquireAssetUrl("/lease/caller");
    const outcome = first.url.catch((error: Error) => error);
    const reason = new DOMException("left", "AbortError");
    controller.abort(reason);
    expect(await outcome).toBe(reason);
    response.resolve(new Response("shared"));
    expect(await other.url).toBe(created[0]);
    other.release();
  });

  it("disposes private assets on scope retirement", async () => {
    const response = Promise.withResolvers<Response>();
    vi.mocked(fetch).mockReturnValue(response.promise);
    const leases = Array.from({ length: 6 }, (_, i) => acquireAssetUrl(`/lease/session-${i}`));
    const outcomes = leases.map((lease) => lease.url.catch((error: Error) => error));
    window.dispatchEvent(new Event("printstash:auth-changed"));
    for (const [, init] of vi.mocked(fetch).mock.calls) expect(init?.signal?.aborted).toBe(true);
    response.resolve(new Response("obsolete"));
    for (const outcome of await Promise.all(outcomes))
      expect(outcome).toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(created).toHaveLength(0);
    leases.forEach((lease) => lease.release());
  });
});

describe("private asset scope", () => {
  it("starts current scope work before an old aborted response settles", async () => {
    const previous = Promise.withResolvers<Response>();
    vi.mocked(fetch)
      .mockReturnValueOnce(previous.promise)
      .mockReturnValueOnce(previous.promise)
      .mockReturnValueOnce(previous.promise)
      .mockReturnValueOnce(previous.promise);
    const old = Array.from({ length: 4 }, (_, i) => acquireAssetUrl(`/lease/old-${i}`));
    const outcomes = old.map((lease) => lease.url.catch((error: Error) => error));
    window.dispatchEvent(new Event("printstash:auth-changed"));
    const current = acquireAssetUrl("/lease/current");
    try {
      expect(fetch).toHaveBeenCalledTimes(5);
      await expect(current.url).resolves.toBeTruthy();
    } finally {
      previous.resolve(new Response("obsolete"));
      current.release();
      await Promise.all(outcomes);
    }
  });

  it("reports encoded byte ownership separately", async () => {
    const lease = acquireAssetUrl("/lease/stats");
    await lease.url;
    expect(getAssetCacheStats()).toMatchObject({
      liveBytes: 9,
      inactiveBytes: 0,
      inactiveEntries: 0,
    });
    lease.release();
    expect(getAssetCacheStats()).toMatchObject({
      liveBytes: 0,
      inactiveBytes: 9,
      inactiveEntries: 1,
    });
  });

  it("excludes referenced image bytes from inactive eviction", async () => {
    vi.mocked(fetch).mockImplementation(async () => new Response(new Uint8Array(8 * 1024 * 1024)));
    const mounted = acquireAssetUrl("/lease/byte-mounted");
    const url = await mounted.url;
    for (let i = 0; i < 5; i++) await getCachedAssetUrl(`/lease/byte-pressure-${i}`);
    expect(revoked).not.toContain(url);
    expect(peekCachedAssetUrl("/lease/byte-mounted")).toBe(url);
    expect(getAssetCacheStats()).toMatchObject({
      liveBytes: 8 * 1024 * 1024,
      inactiveBytes: 32 * 1024 * 1024,
      inactiveEntries: 4,
    });
    mounted.release();
  });
});

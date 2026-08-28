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

import { getCachedAssetUrl, invalidateCachedAsset, peekCachedAssetUrl } from "@/lib/asset-cache";

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
  vi.unstubAllGlobals();
});

describe("assetCache", () => {
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
